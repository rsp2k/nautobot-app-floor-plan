# App Overview

This document provides an overview of the App including critical information and important considerations when applying it to your Nautobot environment.

!!! note
    Throughout this documentation, the terms "app" and "plugin" will be used interchangeably.

## Description

This App is designed to extend Nautobot's built-in Location data model to allow you to define a Floor Plan for each relevant Location. A Floor Plan can be laid out in two ways, and both are supported on the same plan:

- **Grid mode** places objects on a grid of Tiles, each of which has coordinates, an optional Status, and an optional association to an Object belonging to that Location.
- **Freeform mode** places objects at any position over an optional blueprint background image (a real floor plan, CAD export, or architectural drawing), so a marker can sit exactly where the equipment physically is rather than snapping to a grid cell.

The Floor Plan is displayed in the Nautobot UI as a rendered SVG with built-in pan/zoom using your mouse, either with the shift mouse wheel or a click and left click drag box. In freeform mode you can drag markers directly on the plan to reposition them, and drag or rotate the blueprint image to calibrate it against the grid.

Any object type can be placed, not only the built-ins. The App ships with placement support for Devices, Power Panels, Power Feeds, Racks, and Locations, and other apps can register their own object types (a medical device, a camera, an access point) so those appear as first-class placeable objects with their own icon, color, and legend entry. Placeable types and their glyphs can also be defined or overridden from the web UI without a code change.

## Audience (User Personas) - Who should use this App?

The primary user of this App would be anyone involved in the ongoing allocation and usage of data center space or similar, who needs to track the availability of space within a given Location and/or identify the position of Objects within that Location.

## Authors and Maintainers

This App is primarily developed and maintained by Network to Code, LLC.

## App Capabilities

Included is a non-exhaustive list of capabilities beyond a standard MVC (model view controller) paradigm.

- Provides visualization of Objects (Devices, Power Panels, Power Feeds, Racks, and Locations) on a floor map.
- Provides a blueprint background image per Floor Plan, with drag and rotate calibration to align a real floor plan or CAD drawing to the grid.
- Provides freeform placement of objects at any position on the plan, in addition to grid tiles, with drag-to-place directly on the rendered SVG.
- Provides per-type marker icons (glyphs), colors, and an on-plan legend so different object types are distinguishable at a glance.
- Provides marker-visibility **layers** to show, hide, and dim placed objects by content type or by named groups built from tags, dynamic groups, content types, and a hand-picked set (global or per-plan). Status color is never changed.
- Provides marker **sizing** controls: a global icon-size slider that gives every marker one base size regardless of footprint, and a per-marker corner handle to resize an individual marker.
- Provides a plan-first detail view where the rendered canvas is the focus and the editing tools collapse behind a floating **Edit** toolbar.
- Provides the ability to define new placeable object types, glyphs, and colors from the web UI, or to override the built-in ones, without changing code.
- Provides an extension point for other apps to register their own object types as placeable, each with its own icon and color.
- Provides visualization of Power Panels, and Racks being assigned to a Rack Group on a floor map.
- Provides visualization of Tenant and Tenant Groups for Objects on a floor map.
- Provides easy navigation from floor map to Object and subsequently device from Rack.
- Provides easy navigation from floor map via grid labels to filter Rack Elevations.
- Provides easy navigation from Objects to Floor Plan. Objects will be centered and zoomed in for 5 seconds and highlighted on Floor Plan for 20 seconds.
- Provides the ability to assign Objects to coordinates / tiles.
    - From the Floor Plan UI
    - From the Object UI.
    - From the API.
- Provides ability to map status to color for many use cases.
    - Leveraging this you can depict hot / cold aisle.
- Provides the ability to set the direction of the Objects and show up.
- Provides the ability to span multiple adjacent tiles by a single Object.
- Provides the ability to place Objects in a group that spans multiple tiles.
- Provides custom layout size in any rectangular shape using X & Y axis.
- Provides the ability to resize the Floor Plan until Tiles have been placed. Once a Tile has been placed the Floor Plan cannot be resized until the Tiles have been removed.
- Provide the ability to make Tile Objects movable or immovable.
- Provides the ability to choose Numbers or Letters for grid labels.
- Provides the ability to define custom labels for grid labels.
- Provides the ability for a user to define a specific number or letter as a starting point for grid labels.
- Provides the ability for a user to define a positive or negative integer to allow for the skipping of letters or numbers for grid labels.
- Provides the ability to save the generated SVG from a click of a "Save SVG" link.

## Nautobot Features Used

This App:

- Adds a "Location Floor Plans" menu item to Nautobot's "Organization" menu.
- Adds a "Floor Plan Object Types" menu item for defining placeable types, glyphs, and colors from the UI.
- Adds a "Floor Plan Layers" menu item for defining reusable marker-visibility layers.
- Adds new database models, "Floor Plan", "Floor Plan Tile", "Floor Plan Custom Axis Label", "Floor Plan Object Type", and "Floor Plan Layer" (with a through model for a layer's static object set).
- Adds UI and REST API endpoints for performing standard create/retrieve/update/delete (CRUD) operations on these models.
- Extends the detail view of Nautobot Devices, Power Feeds, Power Panels, and Racks.
    - Includes a "View on Floor Plan" button if the Object is on a "Floor Plan Tile".
- Extends the detail view of Nautobot Locations.
    - Includes an "Add/Remove Floor Plan" button.
    - When a Floor Plan is defined a "Floor Plan" tab to display and interact with the rendered floor plan will be present.
    - When a Location has children with a Floor Plan defined a "Child Floor Plan(s) tab is added to display the Child or Children locations.

### Extras

This App presently auto-defines Nautobot extras/extensibility status features. This app automatically assigns the following default statuses for use with Floor plan Tiles. `Active, Reserved, Decommissioning, Unavailable and Planned`.
