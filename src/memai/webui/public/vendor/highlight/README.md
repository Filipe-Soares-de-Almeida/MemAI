# highlight.js, vendored

Browser-ready ES modules from [`@highlightjs/cdn-assets`][pkg] **11.12.0**,
BSD-3-Clause (see `LICENSE`). Vendored rather than fetched: the dashboard is
served from a local file tree with no build step, and it has to work with no
network at all.

`core.min.js` is the engine. Each file under `languages/` is one grammar,
loaded only when a code block asks for it — `core/highlight.js` imports by
name, so **adding a language is dropping its file in here**, and nothing
else. Removing one is the same in reverse: a block whose language has no
file stays plain monospace.

The `es/` build is the one that matters. The `highlight.js` npm package also
ships an `es/` directory, but its core only re-exports CommonJS and will not
load in a browser.

## Updating

```
npm pack @highlightjs/cdn-assets
tar -xzf highlightjs-cdn-assets-*.tgz
cp package/es/core.min.js            <here>/core.min.js
cp package/es/languages/<name>.min.js <here>/languages/
cp package/LICENSE                   <here>/LICENSE
```

Then bump the version at the top of this file.

No theme is taken from the package. The token colours live in `admin.css`
with the rest of the dashboard's palette, so a code block reads as part of
the page rather than as a widget pasted into it.

[pkg]: https://www.npmjs.com/package/@highlightjs/cdn-assets
