# Browser dependencies

These are unmodified files from the published npm packages. Versioned directories
keep browser caches separate when a dependency changes. Both packages use the
Zero-Clause BSD license; each directory includes its upstream `LICENSE`.

| Local file | Package archive | Path inside archive | SHA-256 |
| --- | --- | --- | --- |
| `htmx-2.0.11/htmx.min.js` | [htmx.org 2.0.11](https://registry.npmjs.org/htmx.org/-/htmx.org-2.0.11.tgz) | `package/dist/htmx.min.js` | `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717` |
| `idiomorph-0.8.0/idiomorph-ext.min.js` | [idiomorph 0.8.0](https://registry.npmjs.org/idiomorph/-/idiomorph-0.8.0.tgz) | `package/dist/idiomorph-ext.min.js` | `5811b9a7eda14878b7dab4378bf60432e1bb6f11fcfb970cc26540642650181e` |

`idiomorph-ext.min.js` contains both Idiomorph and its HTMX morph extension.
The archive integrity values from npm were verified before extraction:

- `htmx.org@2.0.11`: `sha512-Thx/WtpeOQqSrqBCw/A1cwGJGg4UrVa3+sW0GmrM3p4gJgO89ecH4qtbnyzDDWFvBTqjnIMCgELTNt636dtamA==`
- `idiomorph@0.8.0`: `sha512-R47JBL+6jHDQvcnbppIUtXXcxi5h6+wuCEEZXnRhUltsgzPFKzThL5PuHppP9ypJuy+7WawcY1TsSBD/iQbMhw==`

For an upgrade, verify the new archive against the registry's `dist.integrity`,
copy its distribution and license into a new versioned directory, update
`templates/base.html` and this table, and remove the superseded directory.
Run `make css`, `make check`, and the browser suite (`make e2e`), including the
external-request-blocked test in `test/e2e/test_local_assets.py`.
