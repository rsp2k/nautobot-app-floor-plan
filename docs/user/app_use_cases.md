# Using the App

This document describes common use-cases and scenarios for this App.

## General Usage

See [Getting Started with the App](./app_getting_started.md) for the basic workflow involved in using this App.

## Grid Floor Plan

The classic use case: a rectangular grid of tiles for a structured data center layout. Objects snap to cells, tiles carry a Status for color coding (for example hot and cold aisles), and grid labels can be customized per axis.

![Add Floor Plan form](../images/add-floor-plan-form-light.png#only-light){ .on-glb }
![Add Floor Plan form](../images/add-floor-plan-form-dark.png#only-dark){ .on-glb }

![Creating Custom Labels](../images/custom_axis_label_preview_light.png#only-light){ .on-glb }
![Creating Custom Labels](../images/custom_axis_label_preview_dark.png#only-dark){ .on-glb }

![Add Tile form](../images/add-tile-form-light.png#only-light){ .on-glb }
![Add Tile form](../images/add-tile-form-dark.png#only-dark){ .on-glb }

![Populated floor plan](../images/floor-plan-populated-light.png#only-light){ .on-glb }
![Populated floor plan](../images/floor-plan-populated-dark.png#only-dark){ .on-glb }

## Freeform Floor Plan on a Blueprint

When you have a real floor plan, use **Import from PDF** to upload the architectural PDF, pick a page, and crop the drawing into the plan's blueprint background (or set a pre-cropped image through the REST API). Put the plan into freeform mode, then drag and rotate the background to calibrate it against the grid, drop markers exactly where equipment sits, and drag them to fine-tune. Each object type renders with its own icon and color, and a legend on the plan keeps them readable.

![Freeform floor plan on a blueprint background](../images/freeform-blueprint-light.png#only-light){ .on-glb }
![Freeform floor plan on a blueprint background](../images/freeform-blueprint-dark.png#only-dark){ .on-glb }

![Calibrating the blueprint image](../images/freeform-calibrate-light.png#only-light){ .on-glb }
![Calibrating the blueprint image](../images/freeform-calibrate-dark.png#only-dark){ .on-glb }

## Focusing on a subsystem with layers

A production floor can hold racks, devices, cameras, access points, and power gear at once. When you want to reason about one system, open the **Layers** panel and hide or dim the rest. You can filter by object type on the spot, or build reusable **Floor Plan Layers** whose membership comes from tags, dynamic groups, content types, or a hand-picked set — for example a "CCTV" layer that follows a tag, or an "AP heat-map" view. Because named layers take precedence over the type toggles, isolating one system is a matter of turning the type toggles off and leaving the layer on. Status colors are never changed, so a faulted marker still reads as faulted even when dimmed.

![Isolating a subsystem with a named layer](../images/layers-isolate.png){ .on-glb }

## Defining Placeable Types and Glyphs

The **Floor Plan Object Types** page lets you decide which object types can be placed and how they look, without touching code. Pick a built-in glyph or supply your own SVG, set a color and legend order, and optionally override a built-in type. This is how you add a vocabulary specific to your environment (for example medical equipment, cameras, or access points) or restyle the defaults.

![Floor Plan Object Type form](../images/object-type-form-light.png#only-light){ .on-glb }
![Floor Plan Object Type form](../images/object-type-form-dark.png#only-dark){ .on-glb }
