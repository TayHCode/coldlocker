from . import agents, claude, codex, cursor, gemini, grok, hermes, opencode, system, t3

PROJECT_STEPS = (
    claude.skills, codex.skills, agents.skills, cursor.skills, grok.skills, cursor.builtin_skills,
    claude.rules, codex.rules, codex.exec_policies, cursor.rules,
    claude.configs, cursor.config, codex.config, claude.desktop_config,
    claude.plugin_registry, claude.plugin_cache, codex.plugin_cache, codex.plugin_registry, cursor.extensions,
    gemini.skills, gemini.rules, gemini.config, gemini.commands, hermes.skills, hermes.rules, opencode.project,
)
GLOBAL_STEPS = (codex.automations, claude.scheduled_tasks, grok.global_sources, gemini.global_sources,
                hermes.global_sources, claude.connections, t3.discover, system.discover)
SEARCHED = {'claude': claude.searched, 'codex': codex.searched, 'cursor': cursor.searched, 'opencode': opencode.searched,
            'agents': agents.searched, 'grok': grok.searched, 'hermes': hermes.searched, 'gemini': gemini.searched,
            't3': t3.searched, 'system': system.searched}
