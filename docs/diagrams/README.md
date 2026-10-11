# Diagrams

The figures in the README, the architecture pages, and decisions 0005 and 0006.

Each figure is two files with the same name:

| File | Purpose |
| --- | --- |
| `<name>.html` | The source. A self-contained page with the figure as inline SVG. Edit this file. |
| `<name>.svg` | The export that the Markdown embeds. Do not edit it by hand. |

The figures follow the [diagram-design](https://github.com/cathrynlavery/diagram-design) conventions in the dark variant: at most nine nodes and twelve arrows per figure, right-angle connectors, and a legend strip at the bottom.

## Change a figure

1. Edit the HTML file. Check every name, port, and address against the code, not only the docs.
2. Run the checks and export the SVG with the scripts from the diagram-design repository:

   ```bash
   python3 scripts/self_check.py docs/diagrams/<name>.html
   python3 scripts/verify-geometry.py docs/diagrams/<name>.html
   python3 scripts/export_svg.py docs/diagrams/<name>.html --system-fonts
   ```

   `--system-fonts` leaves out the web font import, because GitHub blocks remote fonts in images.
3. Open the SVG in a browser and confirm it reads correctly before committing both files.
