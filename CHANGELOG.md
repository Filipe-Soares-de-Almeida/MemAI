# Changelog

## [0.11.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.10.0...v0.11.0) (2026-10-09)


### ⚠ BREAKING CHANGES

* **tasks:** identify task items by id ([#95](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/95))
* **server:** ITEM_MAX drops from 300 to 80, and writes that cite an item key outside DEPENDS ON are refused.

### Features

* **server:** describe every tool for the agents that call it, and let a task be corrected in place ([#90](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/90)) ([abb7d04](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/abb7d04e4c2c782c05661a5fa7d708ea1fdc29bd))
* **tasks:** identify task items by id ([#95](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/95)) ([97e511e](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/97e511edc2b9e5f8f865a7b326960c25ee3becbf))
* **webui:** mention items with # only and always animate the dashboard ([#96](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/96)) ([720e10f](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/720e10fbeefd62f98c50d41ecd285e80bb4281e4))

## [0.10.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.9.1...v0.10.0) (2026-10-09)


### ⚠ BREAKING CHANGES

* **tasks:** hold item notes to a brief template, link their dependencies and number items by position ([#89](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/89))

### Features

* **tasks:** hold item notes to a brief template, link their dependencies and number items by position ([#89](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/89)) ([d0a76a3](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/d0a76a39c007108119e074bdd975c9fda739397d))
* **webui:** move every dashboard view to Vue 3 ([#83](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/83)) ([4c345d9](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/4c345d99a07ae8a637385628657892aa7da71e8f))


### Code Refactoring

* **store:** retire the db facade and the legacy dashboard helpers ([#86](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/86)) ([5a45562](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/5a45562e367b570e6d4aa4e2db303c6a513ede1b))
* **store:** split db.py and admin.py into the store and admin packages ([#85](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/85)) ([a86b887](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/a86b8870b9e80a022b2d053b4ad30720d7dac230))
* **webui:** share constants with Python and type the dashboard and its API client ([#82](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/82)) ([ced7ded](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/ced7ded75ee139bbbb6dc19ce60123b128e384bf))


### Documentation

* **skills:** the bundled skills name all 13 suggestion kinds and no db module ([#88](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/88)) ([e25f46c](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/e25f46cfb8ada838306325ae7ec861b0545be5c7))

## [0.9.1](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.9.0...v0.9.1) (2026-10-05)


### Bug Fixes

* **webui:** draw and edit task notes as record fields ([#80](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/80)) ([0fa4fbf](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/0fa4fbf38c7d67024a62b4920ba027d8e9cc755f))

## [0.9.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.8.0...v0.9.0) (2026-10-05)


### ⚠ BREAKING CHANGES

* **tasks:** keep task notes out of the memory set and page every tool result ([#78](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/78))

### Features

* **tasks:** keep task notes out of the memory set and page every tool result ([#78](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/78)) ([8d6fcb1](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/8d6fcb15537219c29a7ec6f3b7319260b6fa249c))

## [0.8.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.7.0...v0.8.0) (2026-10-05)


### ⚠ BREAKING CHANGES

* **deps:** memai requires mcp>=2.2,<2.3.

### Build System

* **deps:** move the server to mcp 2.2 ([#71](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/71)) ([09b159c](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/09b159cd25f5d49fe8dcb012adbc7294f47fcc1e))

## [0.7.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.6.0...v0.7.0) (2026-10-04)


### ⚠ BREAKING CHANGES

* `npm ci` and install.bat refuse Node below 22.18.
* **server:** the MCP tool help() is removed, and each MEMAI_TOOLS set publishes one tool fewer (full 41, core 26).
* **server:** the MCP tool pending() is now must_read(), with no alias, and pulse() returns `must_read` in place of `pending`. Update the server and rerun `memai-hook install` together, or the guard holds a session waiting for a tool its server does not publish.

### Features

* **server:** remove help() and describe task goals as free-prose briefs ([#66](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/66)) ([eca2e80](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/eca2e80d9a745b2028b39cee7949a0df15c601cd))
* **server:** the tool that lists what a scope still holds to read is must_read() ([#64](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/64)) ([9c58708](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/9c587081c885dd5513f6ab5eabdd55c45e1a7788))


### Build System

* lock dependencies, require Node 22.18 and ship a prebuilt dashboard ([#67](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/67)) ([73b533c](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/73b533c2a5aafbd2c753dcefb64f083f0bce77b5))

## [0.6.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.5.0...v0.6.0) (2026-10-03)


### Features

* **hooks:** hold a briefed session's tools until it reads its memory ([#61](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/61)) ([fd611a3](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/fd611a30570f2686a9f54c5f09d4b44644b2ffc2))


### Documentation

* the host table names the file each Claude host reads, and the README lists every wiki page ([#63](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/63)) ([0292cc5](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/0292cc5ae1b35350a35525f980aef2e2856cb765))

## [0.5.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.4.0...v0.5.0) (2026-10-03)


### Features

* rebuild the dashboard on start and guard install and update against a running MemAI ([#58](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/58)) ([29aa4a5](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/29aa4a51e7b7722e37a95eeb9d88cd05561882be))
* **webui:** highlight breaking changes in the changelog and put Maintenance last in the nav ([#60](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/60)) ([fb4f246](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/fb4f24602019489806122dcbe5fef484734ad59e))

## [0.4.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.3.0...v0.4.0) (2026-10-03)


### ⚠ BREAKING CHANGES

* **tasks:** pulse() no longer returns handoffs, anti_patterns, recent_notes or diagrams; it returns pending counts and read_next.

### Features

* pin memories so every session in their scope reads them first ([#57](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/57)) ([6937aa5](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/6937aa5bbf5167f0ac586ee9f72af7c026620ef9))
* **tasks:** add task checklists and replace handoff and pulse lists with pending() ([#55](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/55)) ([0e4a2b9](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/0e4a2b9d08ddc2d61176cb09867d8cc4b172ab6d))

## [0.3.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.2.1...v0.3.0) (2026-10-01)


### Features

* **admin:** check for a new release on demand and choose how often it is checked ([#54](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/54)) ([aa3521a](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/aa3521aebfca6bf0f81d9739a6670090c4fda225))
* **admin:** delete memories in bulk, choose how backups are zipped, back up each optimizer run ([#53](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/53)) ([82a2ff5](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/82a2ff5a1cef057a17c455b626680dfefdc08e48))


### Documentation

* the release pull request says what the block below it is ([#51](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/51)) ([e5089a0](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/e5089a071e78d72485d8a4a67b8d3e86aa5ee4b7))

## [0.2.1](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.2.0...v0.2.1) (2026-09-18)


### Bug Fixes

* **autostart:** stop the dashboard from hanging on Windows shutdown ([18ac71b](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/18ac71bd4d9c2fb5461c438df4e18877c0a06772))

## [0.2.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.1.1...v0.2.0) (2026-09-17)


### Features

* check for new releases and read the release history in the dashboard ([1647b2e](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/1647b2e5b5c53634bec29da3e389c85251c3929a))


### Bug Fixes

* **webui:** lay the update card out as one numbered instruction ([61a218d](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/61a218da5e9af687f5e3d91c1d5bb6cc64688f11))
* **webui:** let the update card's lead run the width of the card ([5495b42](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/5495b422e16118ef3ae231c2e90e454840c36d6c))

## [0.1.1](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.1.0...v0.1.1) (2026-09-12)


### Bug Fixes

* **ci:** a release tag is vX.Y.Z, not memai-vX.Y.Z ([22b80bc](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/22b80bc178533671c744001b0a036c17ebd53515))
* **ci:** the release workflow states the branch it runs on and holds one run at a time ([eec155d](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/eec155d8c96cbd0f352ffedb36415d966301bb00))


### Documentation

* a pull request states its type and how to verify it ([61403ce](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/61403ce312cbcb9926c1b362b57278abc5845682))
* the pull request template asks for a summary and a test plan ([fc9569a](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/fc9569a90faaa8e731d1b0e54a67ecd10d83fd41))
* the pull request template asks what changed and how to check it ([4154bea](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/4154bea56e2ce7fa118a4e2fd963ccd49f0da9d3))
* the pull request template carries a checklist of what a change has to satisfy ([5239269](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/5239269dd4d6db6fba4daf70737957fbf83d8999))
* the release secret and the template are stated where a fresh clone finds them ([8bbaef1](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/8bbaef1c5c706d9e460b1c7997ba6d8004c7fd44))
