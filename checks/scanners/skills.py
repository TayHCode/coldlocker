from checks import validators
from importers import selected_path, skill_package

from .common import ERRORS, digest, read, tree_hash


class SkillMixin:
    def packages(self, agent, base, vendor=False, builtin=False):
        for path in self.walk(base, depth=12 if vendor else 5):
            if path.name != 'SKILL.md':
                continue
            package = path.parent
            hosted = agent == 'claude' and package.relative_to(base).parts[:1] == ('synced',)
            ownership = 'plugin_cache' if vendor else 'vendor' if builtin or hosted or '.system' in package.parts else 'user'
            try:
                selected_path(base, package.relative_to(base))
                checksum = tree_hash(package)
                if ownership == 'user':
                    skill_package(package)
                self.add(agent, 'skill', package.name, package, package, checksum, ownership)
            except ERRORS:
                if ownership != 'user':
                    self.add(agent, 'skill', package.name, package, ownership=ownership, flags=['needs-manual'], reason=self.cause(package))
                else:
                    self.failed(agent, 'skill', package)

    def instruction_name(self, base, agent, path):
        scope = '' if base == self.home else base.name + '-'
        if path.parent == base:
            return scope + path.stem
        return scope + (path.stem if agent == 'claude' else f'{agent}-{path.stem}')

    def rule(self, agent, path, name=None):
        if not path.exists() and not path.is_symlink():
            return
        try:
            data = self.tilde(read(path).decode()).encode()
            validators.safe_fragment(data.decode())
            self.add(agent, 'rule', name or path.stem, path, data, digest(data))
        except ERRORS:
            self.failed(agent, 'rule', path)
