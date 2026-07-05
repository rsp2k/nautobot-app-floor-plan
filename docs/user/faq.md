# Frequently Asked Questions

## What is the difference between grid mode and freeform mode?

Grid mode snaps every object to a cell in the X/Y grid, which is ideal for a structured data center layout where racks line up in rows. Freeform mode lets an object sit at any position on the plan, which is better when you want a marker to land exactly where equipment physically is over a real floor plan image. New plans start in grid mode; a plan is put into freeform mode through the REST API or by converting an existing grid plan, after which the interactive place, drag, and calibrate tools appear on the rendered plan.

## Do I have to use a background image?

No. The blueprint background is optional. Freeform placement works over a plain grid just as well, and grid mode does not use a background at all. A background image is most useful when you have an actual floor plan, CAD export, or architectural drawing to align markers against.

## Which object types can I place on a Floor Plan?

Out of the box: Devices, Power Panels, Power Feeds, Racks, and Locations. Beyond those, other Nautobot apps can register their own object types as placeable, and you can define or override placeable types yourself from the **Floor Plan Object Types** page in the UI. Any object that resolves to the plan's Location is eligible.

## How do I change the icon or color used for an object type?

Create or edit a record on the **Floor Plan Object Types** page. You can pick a built-in glyph, supply your own SVG paths, set a color, control the legend order, and choose whether your definition overrides the built-in one. Changes apply without restarting Nautobot.

## Why does my object still show its status color instead of the type color I set?

By design, a live object's Status color takes precedence over a type's default color, so a faulted or decommissioning object reads as its status on the plan. The type color is used as the fallback when the object has no status-driven color.

## I moved a marker in freeform mode. Is the new position saved?

Yes. Dragging a marker persists its position through the API as soon as you drop it, so the placement is stored on the Floor Plan Tile, not just visually shifted in your browser.
