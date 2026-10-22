# The jukebox asks the keypad to restart into maintenance mode (its drive
# showing, for an update) by flashing the keyboard lights in a pattern no
# person types: Num, Caps, Scroll, Caps, Num, all three, none -- within a
# few seconds. Pure, so it can be tested off the board.

PATTERN = (1, 2, 4, 2, 1, 7, 0)   # bits: 1 Num Lock, 2 Caps Lock, 4 Scroll Lock
WINDOW_S = 4.0


class Signal:
    def __init__(self):
        self.seen = []        # (lights, time) for each change
        self.last = None

    def feed(self, lights, t):
        """The lights now; True once the whole pattern has been seen."""
        if lights == self.last:
            return False
        self.last = lights
        self.seen = [(v, s) for v, s in self.seen if t - s <= WINDOW_S][-(len(PATTERN) - 1):]
        self.seen.append((lights, t))
        if tuple(v for v, _ in self.seen) == PATTERN:
            self.seen = []
            return True
        return False
