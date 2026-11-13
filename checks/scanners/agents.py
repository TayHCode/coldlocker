
def skills(inv, base):
    inv.packages('agents', base / '.agents/skills')


def searched(inv):
    return [inv.home / '.agents']
