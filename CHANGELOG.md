# Changelog

## [0.16.4](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.16.3...v0.16.4) (2026-11-13)


### Bug Fixes

* confirm a bulk archive once, keep it on screen, and quote spaced paths ([#1](https://github.com/Filipe-Soares-de-Almeida/MemAI/issues/1)) ([95eaf74](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/95eaf74fb8806c1353039f7bb981b0540832d78f))

## [0.16.3](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.16.2...v0.16.3) (2026-11-01)


### Documentation

* name the folder the Windows desktop app reads its MCP config from ([d9d1025](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/d9d10254ff8b3bbafc8d33b3d08855349d4dadec))

## [0.16.2](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.16.1...v0.16.2) (2026-10-16)


### Bug Fixes

* read the whole releases page and stop calling a failed check up to date ([7c04018](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/7c040189cb7b9946b78eb8d4c16e2933976a6f34))

## [0.16.1](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.16.0...v0.16.1) (2026-10-16)


### Bug Fixes

* **server:** a tool's ValueError reaches the caller as its own text, not the SDK's generic error ([93f95ae](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/93f95aefd629c3281c84200620b3b51ce35d8cf8))

## [0.16.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.15.0...v0.16.0) (2026-10-14)


### ⚠ BREAKING CHANGES

* **tasks:** the `items` of task_note() and of a note read with task_read() are lists of {id, n} instead of lists of ids, and a task's content lines read "[ ] n. text (id N)" instead of "[ ] N text".

### Features

* **tasks:** a task's content and every task result name each item by its number beside its id ([08feb7c](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/08feb7c46c361157e0ba3fffc5678f39dba37283))

## [0.15.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.14.0...v0.15.0) (2026-10-12)


### ⚠ BREAKING CHANGES

* **tasks:** the `items` of task() and task_add() and the `doing` of a must_read() task header are lists of {id, n} instead of lists of ids.

### Features

* **tasks:** task results carry each item's number beside its id, and an item is named to a person as item n of its task ([2a3dea4](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/2a3dea4d84f4041935d1d6d2be80cb5402cc06d9))

## [0.14.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.13.0...v0.14.0) (2026-10-12)


### Features

* offer one- and two-day spans for the checkpoint block and the confidence reminder ([aac1ddf](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/aac1ddf83731a8dfb06c2f240571dbabadaf6286))

## [0.13.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.12.0...v0.13.0) (2026-10-12)


### Features

* keep one checkpoint per domain, block writes under overdue ones and remind about unverified memories ([03c6538](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/03c6538e37dcd9a3b95deaa83fc1af0922d54cae))

## [0.12.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.11.0...v0.12.0) (2026-10-10)


### Features

* count the field ceilings in the dashboard and show tags in must_read headers ([4930a1e](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/4930a1e2912ce9e10a7197047675bd24283a733b))


### Bug Fixes

* **server:** keep every tool result inside the output ceiling ([9042d83](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/9042d83286099353348281e32cc21718a722e7c8))
* **server:** keep optimize_scan inside the result ceiling for any store ([3ca9e1e](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/3ca9e1ee31f9d84765020d7226f68fe36ac62e0a))
* **webui:** the domain picker opens with its filter field ([31ba6dc](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/31ba6dc7d47041a0d8692fdcef22ac2ef7a38793))

## [0.11.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.10.0...v0.11.0) (2026-10-09)


### ⚠ BREAKING CHANGES

* **tasks:** identify task items by id
* **server:** ITEM_MAX drops from 300 to 80, and writes that cite an item key outside DEPENDS ON are refused.

### Features

* **server:** describe every tool for the agents that call it, and let a task be corrected in place ([76c0ce2](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/76c0ce20a9e154a1f2d02e8df8f1352c4395ed4c))
* **tasks:** identify task items by id ([794b879](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/794b879e0f6e04152fb9942066b0ef9d1fbfca60))
* **webui:** mention items with # only and always animate the dashboard ([98107e4](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/98107e488c38e77b4772c36246fea27287ece161))

## [0.10.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.9.1...v0.10.0) (2026-10-09)


### ⚠ BREAKING CHANGES

* **tasks:** hold item notes to a brief template, link their dependencies and number items by position

### Features

* **tasks:** hold item notes to a brief template, link their dependencies and number items by position ([8e9c2f6](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/8e9c2f6a52ddefbb98f5df729167dc8e670e138f))
* **webui:** move every dashboard view to Vue 3 ([7f0b96b](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/7f0b96b334c825e0272b5b508576566341daef65))


### Code Refactoring

* **store:** retire the db facade and the legacy dashboard helpers ([e43c71d](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/e43c71ddd1c40679f04f5fb5b931a2073e7ff66a))
* **store:** split db.py and admin.py into the store and admin packages ([0fecfc3](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/0fecfc344e2ad4856cbdbe3922825649baa6ac3d))
* **webui:** share constants with Python and type the dashboard and its API client ([0e0cc8c](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/0e0cc8c8a2a7f41ff11ab3b0d276126333d1bb14))


### Documentation

* **skills:** the bundled skills name all 13 suggestion kinds and no db module ([68db526](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/68db5262f0b8ae93f00d4c188119a4359f62a07d))

## [0.9.1](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.9.0...v0.9.1) (2026-10-05)


### Bug Fixes

* **webui:** draw and edit task notes as record fields ([6bff91f](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/6bff91fd4be4d2cad63bc547d7132e0a4f9876a3))

## [0.9.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.8.0...v0.9.0) (2026-10-05)


### ⚠ BREAKING CHANGES

* **tasks:** keep task notes out of the memory set and page every tool result

### Features

* **tasks:** keep task notes out of the memory set and page every tool result ([03baf90](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/03baf9026049b43a581ae45880ba734a98da2783))

## [0.8.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.7.0...v0.8.0) (2026-10-05)


### ⚠ BREAKING CHANGES

* **deps:** memai requires mcp>=2.2,<2.3.

### Build System

* **deps:** move the server to mcp 2.2 ([09dd306](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/09dd30694f93a7ff48af5e84b01fdde926cfe54b))

## [0.7.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.6.0...v0.7.0) (2026-10-04)


### ⚠ BREAKING CHANGES

* `npm ci` and install.bat refuse Node below 22.18.
* **server:** the MCP tool help() is removed, and each MEMAI_TOOLS set publishes one tool fewer (full 41, core 26).
* **server:** the MCP tool pending() is now must_read(), with no alias, and pulse() returns `must_read` in place of `pending`. Update the server and rerun `memai-hook install` together, or the guard holds a session waiting for a tool its server does not publish.

### Features

* **server:** remove help() and describe task goals as free-prose briefs ([de1505e](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/de1505e5db204062a7168bc4475f1c8bbd3ec464))
* **server:** the tool that lists what a scope still holds to read is must_read() ([c919171](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/c919171492bc8ae546a39cf27cb51d7938331aa4))


### Build System

* lock dependencies, require Node 22.18 and ship a prebuilt dashboard ([708c744](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/708c744ac3fe95f00fbeaddb9478ea20a6fef1dc))

## [0.6.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.5.0...v0.6.0) (2026-10-03)


### Features

* **hooks:** hold a briefed session's tools until it reads its memory ([54d67d9](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/54d67d9e7e8995905f3aa2059301b292832f7391))


### Documentation

* the host table names the file each Claude host reads, and the README lists every wiki page ([92ba871](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/92ba871ef54a92582e8ee35b396ea4c825eec3d8))

## [0.5.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.4.0...v0.5.0) (2026-10-03)


### Features

* rebuild the dashboard on start and guard install and update against a running MemAI ([1becd75](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/1becd750c4457e6cc1a92bd93b97cac5f5ab6301))
* **webui:** highlight breaking changes in the changelog and put Maintenance last in the nav ([cab359a](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/cab359a4c9750e8ef59459f2c83ea1ff8c31fd1b))

## [0.4.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.3.0...v0.4.0) (2026-10-03)


### ⚠ BREAKING CHANGES

* **tasks:** pulse() no longer returns handoffs, anti_patterns, recent_notes or diagrams; it returns pending counts and read_next.

### Features

* pin memories so every session in their scope reads them first ([90a61a4](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/90a61a416c9eb0fd619d7a4aff31be106445337a))
* **tasks:** add task checklists and replace handoff and pulse lists with pending() ([7168bce](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/7168bceee3ef1869728ffd192178775a7d8c24be))

## [0.3.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.2.1...v0.3.0) (2026-10-01)


### Features

* **admin:** check for a new release on demand and choose how often it is checked ([3178697](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/3178697d313d237d282c48ec8760ab8f47305fd4))
* **admin:** delete memories in bulk, choose how backups are zipped, back up each optimizer run ([a357bf7](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/a357bf75b8c8c6321538cf8838028cb0c85bbd95))


### Documentation

* the release pull request says what the block below it is ([284e6fe](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/284e6fef8894b525ee66a70a1fbec8e659968915))

## [0.2.1](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.2.0...v0.2.1) (2026-09-18)


### Bug Fixes

* **autostart:** stop the dashboard from hanging on Windows shutdown ([bb3fc84](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/bb3fc84349d09ed28e2fd7554be7a68246095529))

## [0.2.0](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.1.1...v0.2.0) (2026-09-17)


### Features

* check for new releases and read the release history in the dashboard ([4267ef5](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/4267ef5c56396ce91555c5edfd28adb78e63310c))


### Bug Fixes

* **webui:** lay the update card out as one numbered instruction ([b81357f](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/b81357fb0392dd6b41eabf57b06b448218b08afb))
* **webui:** let the update card's lead run the width of the card ([7d871b6](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/7d871b64c03acfb59a07031717466bf0bb3a2cfe))

## [0.1.1](https://github.com/Filipe-Soares-de-Almeida/MemAI/compare/v0.1.0...v0.1.1) (2026-09-12)


### Bug Fixes

* **ci:** a release tag is vX.Y.Z, not memai-vX.Y.Z ([b6103c1](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/b6103c1001bd07e3dd8910bdfd4c218e0a1dad2c))
* **ci:** the release workflow states the branch it runs on and holds one run at a time ([ce4dab0](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/ce4dab02c0f9ac320b444d50082ca5eeeb54d8b0))


### Documentation

* a pull request states its type and how to verify it ([9052926](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/90529266243b37488bbf6da45335c277bd8fce58))
* the pull request template asks for a summary and a test plan ([94e78a7](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/94e78a7c106005b240b1e497a820196ebb03856d))
* the pull request template asks what changed and how to check it ([fe3c465](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/fe3c4659931e1c06b0b8fac823b8e2c7235d03e8))
* the pull request template carries a checklist of what a change has to satisfy ([88c22ef](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/88c22ef6486c26fc88fa41f6d0b777fa0de89cd7))
* the release secret and the template are stated where a fresh clone finds them ([265c918](https://github.com/Filipe-Soares-de-Almeida/MemAI/commit/265c918dd3e9cb857f918316eb7df9ead4a9fe36))
