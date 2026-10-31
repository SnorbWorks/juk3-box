"""The engine's layers: what modules' lights do on top of the look.

Every frame, each chain stacks (docs/contracts/lights.md, Layers):
  base      the look: the idle look, or the music show
  signals   modules' palettes tint it, their intensity scales it
  cues      named one-offs (lightning, a doorbell) as short effects
  overlays  a module taking part of the cabinet for a while
  feedback  the keypad's own (a pick, Reset, the power menu): always wins,
            drawn by buttons-theme1 outside these layers
then the safety pass (safety.py).
"""

# What a cue can turn into (a show's inputs.cues maps cue names to these).
CUE_EFFECTS = {
    "flash",      # the whole cabinet white, fading (a flash: counted against the flash limit)
    "flicker",    # lightning: two or three quick white flickers
    "pulse",      # one swell of the cue's colour
    "sparkle",    # a shower of sparkles in the cue's colour
    "sweep",      # a band of the cue's colour crossing the cabinet
    "ignore",     # this show doesn't react to it
}
