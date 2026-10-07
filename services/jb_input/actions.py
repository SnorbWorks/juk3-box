"""Stage 4: gesture -> action, a pure table lookup from config."""

from services.jb_input.events import Action, Gesture


class ActionTable:
    def __init__(self, table: dict):
        self.table = dict(table)  # (control, gesture) -> action name

    def has(self, control: str, gesture: str) -> bool:
        return (control, gesture) in self.table

    def lookup(self, g: Gesture) -> Action | None:
        name = self.table.get((g.control, g.gesture))
        if name is None:
            return None
        return Action(name, g.steps, g.control, g.gesture, g.t)
