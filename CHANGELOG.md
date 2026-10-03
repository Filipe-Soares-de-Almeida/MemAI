# Changelog

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
