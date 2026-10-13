# Platform — Development Plan

Oct 12, 2026 · @shawn

A standalone plan for the platform layer. Its job is to get the contracts right so the platform stays robust and is never chained to early decisions — the trap that WordPress carries from 2004.

## Reading note

This document describes the platform in principles and contract shapes, not current implementation. It deliberately does not treat any other plan as ground truth. The jukebox build has moved past its written plan, so specific service names, pin assignments, pixel counts and board revisions are treated as stale and are never cited here as fact.

The jukebox appears only as *the first reference implementation* — an example that proves the contracts, never a source of fixed decisions. Where an example is needed, it is named as illustrative and nothing downstream depends on it. This is also good platform design: keeping the plan decoupled from a moving target is the same discipline that keeps the platform decoupled from any one build.

## What the platform is

The product is not a jukebox. It is an intelligent media platform: a thin core plus a common language, on top of which modules bring in content, understand it, control it, and present it. The jukebox is the first thing built with it — a reference implementation and a demo, not the thesis. The real value is themability: the platform decides what is *possible* to build, not what you build.

The analogy is WordPress, and it is a close one. WordPress won not by being the best blogging tool but by separating content, presentation and behavior early, and exposing clean extension points — themes, plugins, hooks — so other people's code rides along without touching the core. The same separation is the goal here.

The lesson to avoid is also WordPress's. Its hardest problems were never the page builder or the themes; they were the platform problems — a stable contract that does not break modules on update, configuration a non-programmer can use, security when arbitrary extensions run, and backward compatibility that chains the core to old decisions. The moment others depend on an interface, the freedom to change it is gone. So the highest-leverage work now — while it is still one builder and one reference implementation — is defining and freezing the contracts, not building the configuration UI.

## The thin core

The core is almost nothing: a **bus**, the **contracts**, and a **registry** of which modules are present. It is a switchboard, not a brain. All intelligence, control and rendering live in modules. The core's only job is to let modules find each other and speak a common language without knowing each other's names.

**The load-bearing rule: the core knows about no specific module.** It cannot assume a keyboard exists — some builds use a touchscreen. It cannot assume audio — some builds play video. It cannot assume a light show, a song board, or any named service. The instant the core knows a specific module, that assumption is welded in forever, which is exactly the decision-chain that haunts WordPress. A good test: if a change to one module forces a change to the core, the boundary was drawn in the wrong place.

## The four module roles

Modules are categorized by what they do in the pipeline, not by what they are. Every role is optional and swappable; a build uses any mix of them, and the core requires none.

- **Sources** — bring content in. A source presents a library and plays items. Music is the first; video, audiobooks, podcasts and photos are future sources.
- **Analyzers / processors** — take content and produce understanding about it, enriching the stream. The audio analyzer is the first; a video scene detector would be another.
- **Inputs / control surfaces** — let a human drive the system. The platform must not assume which. Physical buttons are the first; a touchscreen, phone remote, voice or IR remote are all the same role, different hardware.
- **Outputs / presentations** — render state to the world. The kiosk song board and the light show are the first; a video output, a now-playing screen or an ambient visualizer are others.

The design target is that all four are optional and swappable, and the core knows about none of them specifically. "Home entertainment system that does everything" is also where ambitious platforms die of infinite generality — so the roles are defined now, but each contract is proven against the one reference implementation before anything is frozen (next section).

## Contract-first discipline

The platform work that is cheap now and expensive later is making the contracts explicit. Build the data model and the extension points right, and the backend configuration tool becomes a view onto them. Build the UI first and let the contracts stay implicit, and the UI gets rewritten every time the underlying shape shifts. In WordPress terms: get the hooks and the data model right and the admin dashboard is just a view; build the dashboard first and the hooks get painted into whatever corner it assumed.

Three rules carry the whole plan:

- **Build every reference module as if a stranger's module sits next to it on the bus.** Name events as public interfaces. The bus is the hook system — a third-party module subscribes and publishes rather than modifying core code.
- **Keep configuration in data, not code.** Anything a builder would want to change lives in config a backend tool can edit, never in a module's source.
- **Do not freeze a contract against a single case.** One source teaches nothing about whether the source contract is right. Design the role now, prove the contract against the reference implementation, and hold generalization until a *second* real case forces it. Generality invented ahead of a second case is guesswork that becomes permanent.

## Contracts to define

These are the interfaces to pin down and document, in priority order. Each is proven against the first reference implementation and left deliberately open where a second case is still needed.

- **The bus.** What rides on it, how messages are named and namespaced, fast versus slow channels, who may publish what. This is the hook system and the most important contract.
- **Module lifecycle and registry.** How a module declares itself, announces its role and capabilities, is discovered, and is configured — so the core knows what is present without knowing what any of it is.
- **The role contracts.** For each of the four roles, the minimum interface a module must satisfy: what a source answers (browse, play item, now-playing), what an analyzer publishes, what an input emits, what an output consumes.
- **The configuration model.** The shape of config itself — how a whole build is described in data so the backend tool can compose modules without touching code.
- **Universal content model — FLAGGED, resolve on second content type.** A song, a video and an audiobook need one common shape to flow through the system. Deliberately *not* designed now: one content type cannot tell us the right shape, and guessing it against music alone would bake in the wrong assumptions — the exact trap. Define it when the second content type actually arrives (video, audiobook, or even a second music source) and forces the real requirements.

## Build order

1. **Contracts on paper.** Write the bus schema, module lifecycle and registry, role contracts and configuration model as explicit documents. No platform code yet — the reference implementation is the proving ground.
2. **Prove the contracts against the reference implementation.** Confirm the existing modules fit the role contracts cleanly, each built as a public, bus-speaking, config-driven module. Where one does not fit, the contract is wrong — fix it now, while it is cheap.
3. **Harden the extension points.** Make the registry and lifecycle real: a module can be added, declared and configured without core changes. Stable contract versioning so updating the core does not break modules.
4. **The backend configuration tool.** The WordPress-style admin: compose modules and themes from data. This is a view onto the step-1 contracts, built only after they are stable.
5. **Second-case validation.** Add a genuinely different module to test each contract against reality — a second source, a non-keyboard input, a second output. This is where the flagged content model gets resolved, and the real test of whether the contracts generalize.

This is a separate, large effort from the reference build, most of it the unglamorous generalization and documentation work. It should not be underestimated — and it does not block the reference implementation, which ships as the platform's first proof regardless. Build it as the lens, architect it like the goal: the clean boundaries already being drawn do most of the work.

## Progress

Oct 13, 2026

1. **Contracts on paper: done (draft v1).** [Bus](contracts/bus.md), [modules, registry and lifecycle](contracts/modules.md), [configuration](contracts/configuration.md), alongside the existing [audio analysis](contracts/audio-analysis.md) contract. Written from what the reference build does, each with proving notes and what's held open for a second case.
2. **Proving against the reference build: first pass done.** Every module fits a role and declares itself (`services/*/module.json`). Gaps found and noted in the contracts: only the analyzer stamps `v`; the source contract is phone-specific in name (held for a second source); jb-lights doesn't heartbeat yet.
3. **Extension points: partly real.** Discovery is real: the registry is built from installed manifests plus bus heartbeats, and a module appears by being installed. Owner layouts and settings are data with a single writer and a change event. Not yet: contract versioning checks, a plugin folder in use, per-module owner-settings schemas (waiting for a second module that needs one).
4. **Backend configuration tool: first version built** as a view onto those contracts: jb-admin (Dashboard, Page Builder, Themes, Media, Settings, Modules, Account). It composes from data: the block schema (`services/jb_ui/block_schema.json`), the settings schema, the module manifests. It knows no specific module beyond the jukebox's own settings.
5. **Second-case validation: not started.**
