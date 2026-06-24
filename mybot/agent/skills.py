class SkillsLoader:
    def __init__(self, workspace=None, *, disabled_skills=None):
        self.workspace = workspace
        self.disabled_skills = disabled_skills or set()
