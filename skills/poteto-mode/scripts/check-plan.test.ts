import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { expect, test } from "bun:test";

const checker = join(import.meta.dir, "check-plan.mjs");
const rule =
	"Tests alone are not sufficient verification. A PR is verified only when its unit, live, and perf boxes are all checked.";

const validPlan = `# Demo

## How to read this
One box is one unit of work and names the evidence. Check a box only when its evidence exists. See playbooks/.
${rule}

## Program checklist
### Arm the program
### Spawn owners
### PR mechanics
### Verdict and merge
### Boot recipe
\`git show origin/main:\` /loop 1h status message

## PR 1
**Depends on.** None.
**Files.**
- [ ] Do the thing.
**Build.**
- [ ] Do the thing.
**You see.**
- [ ] Do the thing.
**Verify, unit.** ${rule}
- [ ] Check the thing.
**Verify, live.** ${rule} Self-run checks at the PR head
${Array.from({ length: 10 }, (_, i) => `- [ ] Lane ${i + 1}. Save \`lane-${i + 1}.png\`. Pass when evidence exists.`).join("\n")}
**Verify, perf.** ${rule}
- [ ] Metric. Recorded.
- [ ] Probe. Recorded.
- [ ] Baseline. Recorded.
- [ ] Rule. Recorded.
**Review gate.** None.
**Merge.**
- [ ] Merge when approved.

## Close the program
Close.

## Appendix Prototype evidence
Recorded.
`;

const runPlan = (example: string, expectedExit: number, plan = validPlan) => {
	const directory = mkdtempSync(join(tmpdir(), "check-plan-test-"));
	const file = join(directory, "plan.md");
	writeFileSync(file, plan + example);
	try {
		const result = spawnSync("node", [checker, file], { encoding: "utf8" });
		expect(result.status).toBe(expectedExit);
		return result;
	} finally {
		rmSync(directory, { recursive: true, force: true });
	}
};

test("accepts ordinary, indented, and longer closing fences", () => {
	for (const indent of ["", " ", "  ", "   "])
		runPlan(`${indent}\`\`\`markdown\n## Example only\n${indent}\`\`\`\n`, 0);
	runPlan("```markdown\n## Example only\n````\n", 0);
});

test("accepts nested backticks and tilde info strings", () => {
	runPlan("````markdown\n```markdown\n## Example only\n```\n````\n", 0);
	runPlan("~~~markdown `info`\n## Example only\n ~~~\n", 0);
});

test("keeps mismatched, shorter, and suffixed closers inside the fence", () => {
	runPlan("````markdown\n~~~\n## Example only\n````\n", 0);
	runPlan("````markdown\n```\n## Example only\n````\n", 0);
	runPlan("````markdown\n``` info\n## Example only\n````\n", 0);
});

test("rejects headings and prose outside a proper close", () => {
	for (const suffix of ["  ", "\t"]) {
		const heading = runPlan(`\`\`\`markdown\n## Example only\n\`\`\`${suffix}\n## Example only\n`, 1);
		expect(heading.stderr).toContain('"## Example only" after Close the program is not an appendix');
	}

	const prose = runPlan("```markdown\nThis prose has a mid-sentence colon: inside code\n```\nThis prose has a mid-sentence colon: bad.\n", 1);
	expect(prose.stderr).toContain("mid-sentence colon");
});

test("rejects backtick info strings containing a backtick", () => {
	const result = runPlan("```markdown `invalid`\n## Example only\n```\n", 1);
	expect(result.stderr).toContain('"## Example only" after Close the program is not an appendix');
});


test("accepts self-run coverage without fixed lane counts", () => {
	for (const count of [1, 12]) {
		const lanes = Array.from({ length: count }, (_, i) =>
			`- [ ] Lane ${i + 1}. Save \`lane-${i + 1}.png\`. Pass when evidence exists.`
		).join("\n");
		const plan = validPlan.replace(/^- \[ \] Lane .+\n/gm, "")
			.replace("**Verify, perf.**", lanes + "\n**Verify, perf.**");
		runPlan("", 0, plan);
	}
});

test("requires builder exclusions when independent verification is selected", () => {
	const routed = validPlan.replace("Self-run checks",
		"Independent checks through `auditor/verifier`, resolved live with every builder excluded");
	runPlan("", 0, routed);
	for (const plan of [
		routed.replace("auditor/verifier", "builder-default"),
		routed.replace(", resolved live with every builder excluded", ""),
	]) {
		expect(runPlan("", 1, plan).stderr).toContain("live auditor/verifier route with every builder excluded");
	}
});

test("rejects missing or nonconsecutive coverage lanes", () => {
	for (const plan of [
		validPlan.replace(/^- \[ \] Lane .+\n/gm, ""),
		validPlan.replace("Lane 2.", "Lane 3."),
	]) {
		expect(runPlan("", 1, plan).stderr).toContain("lanes must be numbered consecutively from 1");
	}
});

 test("accepts saved non-screenshot evidence", () => {
	runPlan("", 0, validPlan.replaceAll(".png", ".log"));
});
