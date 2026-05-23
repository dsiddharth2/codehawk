# TypeScript Review Rules

## All Versions

- [ ] `any` type not used — use `unknown` and narrow, or define a proper type
- [ ] Type assertions (`as T`) not used to paper over type errors without a comment explaining why
- [ ] `!` non-null assertions only used when null is provably impossible at that point
- [ ] Function return types explicitly annotated on public/exported functions
- [ ] `interface` preferred over `type` for object shapes that will be extended
- [ ] Enums not used for string unions — use `const` object + `typeof` or plain string union
- [ ] Generic constraints (`<T extends Foo>`) used to avoid unconstrained generics on public APIs
- [ ] `Partial<T>` / `Required<T>` / `Readonly<T>` used rather than redefining shaped types
- [ ] `noImplicitAny` and `strictNullChecks` enabled in tsconfig — not suppressed with `// @ts-ignore`
- [ ] Discriminated unions used for state modeling instead of optional fields that imply states
- [ ] `as const` used for literal type inference on constant data
- [ ] Re-exporting types uses `export type` to avoid runtime module side effects
- [ ] Index signatures (`[key: string]: T`) document their intent — not used as a catch-all escape
- [ ] `// @ts-ignore` replaced with `// @ts-expect-error` which fails when the error is fixed
- [ ] Utility types (`Pick<T, K>`, `Omit<T, K>`) used for API contract subsets — not manual re-definition
- [ ] Union types narrowed with exhaustive checks (switch with `never` default) to catch missing cases at compile time

## TS 4.x

- [ ] Template literal types used for string-pattern constraints on API route strings or event names
- [ ] `infer` keyword in conditional types accompanied by a comment explaining the inference
- [ ] Variadic tuple types (`[...T]`) used instead of manual overload chains where applicable
- [ ] `noUncheckedIndexedAccess` enabled or array access guarded with bounds check
- [ ] Mapped types with `as` clause used for key remapping instead of manual type construction
- [ ] `Awaited<T>` utility type used to unwrap Promise types instead of manual inference

## TS 5.x

- [ ] `const` type parameters (`<const T>`) used to preserve literal types in generic inference
- [ ] Decorator declarations use the TC39 stage 3 form, not the legacy `experimentalDecorators` form, where possible
- [ ] `satisfies` operator used to validate a value's shape without widening its type
- [ ] `using` / `await using` (explicit resource management) used for `Disposable` objects when targeting ES2022+
- [ ] `override` keyword used on methods that override base class methods (enforces intent)
- [ ] `--verbatimModuleSyntax` used in new projects to prevent re-exporting type-only imports as values
- [ ] Isolated modules mode (`isolatedModules: true`) enabled when using Babel or SWC as transpiler
- [ ] `paths` aliases in tsconfig matched by the bundler/module resolver — mismatches cause runtime failures
- [ ] Declaration files (`.d.ts`) not edited manually when generated from source — edit the source and regenerate
