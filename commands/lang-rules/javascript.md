# JavaScript Review Rules

## All Versions

- [ ] No `eval()` or `new Function(string)` with user-controlled input
- [ ] `==` not used for equality — always `===`
- [ ] `var` not used — use `const` or `let`
- [ ] Promises have `.catch()` handlers or are returned to a caller that handles rejection
- [ ] No swallowed errors in `try/catch` blocks without at least logging
- [ ] Global variables not introduced via implicit `window.myVar = ...`
- [ ] No mutation of function arguments (avoid side-effects on inputs)
- [ ] `typeof` used before accessing properties on potentially-undefined values
- [ ] JSON parsed with try/catch around `JSON.parse()`
- [ ] `innerHTML` not set with unsanitized user content (XSS risk)
- [ ] Event listeners removed when component/element is destroyed to prevent memory leaks
- [ ] `console.log` / `console.error` not left in production code paths
- [ ] No circular `require()` / `import` dependencies
- [ ] `null` and `undefined` handled distinctly where both are possible values
- [ ] Large arrays not iterated with `Array.prototype.reduce` when a simple loop is clearer

## ES2017-ES2019

### Available
- [ ] `async/await` available (ES2017) — prefer over `.then()` chains
- [ ] `Object.entries()` / `Object.values()` available (ES2017)
- [ ] `String.padStart()` / `String.padEnd()` available (ES2017)
- [ ] `Object.getOwnPropertyDescriptors()` available (ES2017)
- [ ] Rest/spread properties on objects available (ES2018): `const { a, ...rest } = obj`
- [ ] `Promise.finally()` available (ES2018)
- [ ] `for await...of` available (ES2018) for async iteration
- [ ] `Array.flat()` / `Array.flatMap()` available (ES2019)
- [ ] `Object.fromEntries()` available (ES2019)
- [ ] `String.trimStart()` / `String.trimEnd()` available (ES2019)
- [ ] `try { } catch { }` without binding available (ES2019)

### DO NOT suggest (not available pre-ES2020)
- [ ] No optional chaining (`?.`) — use manual null checks: `obj && obj.prop && obj.prop.method()`
- [ ] No nullish coalescing (`??`) — use ternary or `||` (but beware of `0`/`""` being falsy with `||`)
- [ ] No `Promise.allSettled()` — use `Promise.all()` with individual `.catch()` wrappers
- [ ] No `globalThis` — use `window` (browser) or `global` (Node)
- [ ] No `BigInt` literals (`123n`)
- [ ] No `String.matchAll()` — use regex with `exec()` in a loop
- [ ] No `import.meta` — use `__dirname` / `__filename` in Node
- [ ] No `Array.at()` (ES2022) — use `arr[arr.length - 1]` for last element
- [ ] No `Object.hasOwn()` (ES2022) — use `Object.prototype.hasOwnProperty.call(obj, key)`
- [ ] No `structuredClone()` — use `JSON.parse(JSON.stringify(obj))` or lodash `cloneDeep`
- [ ] No `String.replaceAll()` (ES2021) — use regex with global flag: `str.replace(/pattern/g, replacement)`
- [ ] No private class fields (`#field`) (ES2022) — use underscore convention `_field`
- [ ] No logical assignment operators (`&&=`, `||=`, `??=`) (ES2021)
- [ ] No `Error` cause option (`{ cause: err }`) (ES2022)

### Node.js 12.x constraints (if applicable)
- [ ] No `fs/promises` — use `util.promisify(fs.readFile)` or callbacks
- [ ] No `AbortController` — use manual cancellation flags
- [ ] No optional chaining in Node-executed code (only in webpack-transpiled browser code)
- [ ] No top-level `await`
- [ ] No `worker_threads` stable API for CPU-bound work (experimental in 12)

## ES2020+

- [ ] Optional chaining (`?.`) used instead of manual null checks for deep property access
- [ ] Nullish coalescing (`??`) used instead of `||` where `0` or `""` are valid values
- [ ] `Promise.allSettled()` used when all results are needed regardless of failure
- [ ] `BigInt` used for integer values exceeding `Number.MAX_SAFE_INTEGER`
- [ ] Dynamic `import()` used for code-splitting large modules in browser environments
- [ ] `globalThis` used instead of `window` / `global` for cross-environment code

## ES2022+

- [ ] Private class fields (`#field`) used instead of convention-based `_field` for encapsulation
- [ ] `Array.at(-1)` used instead of `arr[arr.length - 1]` for last-element access
- [ ] Top-level `await` only used in modules where the async dependency graph is understood
- [ ] `Object.hasOwn()` used instead of `Object.prototype.hasOwnProperty.call()`
- [ ] Error cause (`new Error("msg", { cause: err })`) used when re-throwing wrapped errors
- [ ] Static class blocks (`static { ... }`) used for one-time static initialization instead of IIFE patterns
- [ ] `Array.prototype.findLast()` / `findLastIndex()` used for reverse searches instead of manual `[...arr].reverse().find()`
- [ ] `WeakRef` and `FinalizationRegistry` used only when the semantics are understood — not for general caching
- [ ] Class `#privateField` not accessed via `Object.getOwnPropertyNames` workarounds — treat as a true encapsulation boundary
