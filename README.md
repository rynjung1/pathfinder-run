# Pathfinder Run

A running app that generates scenic, non-repeating loop routes based on a target distance — prioritizing sidewalks and park paths instead of just shortest-path directions.

## Why

Most running apps either give turn-by-turn point-to-point directions or make you manually draw your own loop. Pathfinder Run generates the loop for you: give it a distance, and it builds a route that favors sidewalks/parks, avoids doubling back over the same segment repeatedly, and (eventually) stays aware of closures.

## Status

Early development — see [`docs/architecture.md`](docs/architecture.md) for the full technical plan and current build phase.

## v1 scope

- Self-hosted routing engine (GraphHopper) with a custom pedestrian profile, single city
- Core loop-generation algorithm with an edge-reuse penalty
- Mobile client: request a route, view it on a map
- `WhenInUse` location only — no background tracking, no closures layer yet (both are v2/v3)

## Stack

- Backend: GraphHopper + PostGIS, OSM data
- Mobile: TBD (native iOS/Android or React Native)

## Setup

_TBD as the project takes shape._

## License

_TBD_
