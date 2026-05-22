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
