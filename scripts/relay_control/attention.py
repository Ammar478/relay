"""The attention band that sits between the status bar and the panes.

**Owned by the ACC-TUI-005 leg.** `tui-skeleton` settles the seam and nothing
else: the band is not a view, it has no keybar and it takes no keys, so it does
not belong in `app.VIEWS`. What it has instead is a height that `app.paint()`
asks for before it lays out the Overview, so that adding the band later moves
the panes down without any pane knowing it happened.

The contract, for the leg that fills this in:

    height(model, width) -> int
        Rows the band wants at this width, `0` when the model carries no
        attention items. `app.paint()` clamps the answer to the room actually
        available, so returning an honest figure is enough; it never has to be
        defensive about the terminal being short.

    draw(canvas, model, state) -> None
        Paint into exactly `height()` rows. The canvas is the band's own
        rectangle, so row 0 here is the first row under the status bar.

`model["attention"]` is a list of `{"level", "label", "text", "action"}` already
sorted worst-first by the model; `theme.attention(level)` is the token for each.
`NEEDS YOUR CALL` items are `level == "bad"`.
"""


def height(model, width):
    """Rows the attention band needs. Zero until the ACC-TUI-005 leg lands."""
    return 0


def draw(canvas, model, state):
    """Nothing yet — see the module docstring."""
    return
