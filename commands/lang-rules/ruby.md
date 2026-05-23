# Ruby Review Rules

## All Versions

- [ ] No hardcoded credentials, tokens, or secrets — use environment variables
- [ ] `eval` / `instance_eval` / `class_eval` not called with user-controlled strings
- [ ] `system()` / backtick execution not called with unsanitized user input (shell injection)
- [ ] `rescue Exception` not used — rescue `StandardError` or a specific subclass
- [ ] `rescue` in a loop does not swallow errors silently — at minimum log the exception
- [ ] Mutable default arguments not used in method signatures (same as Python)
- [ ] `attr_accessor` used only for attributes that genuinely need both get and set — prefer `attr_reader`
- [ ] Symbols used for hash keys that are fixed identifiers — strings for dynamic/user-defined keys
- [ ] `freeze` called on string literals used as hash keys or constants
- [ ] `N+1` queries avoided — use eager loading (`.includes`, `.preload`, `.eager_load`) in ActiveRecord
- [ ] Regular expressions validated on a fixture before use in production code
- [ ] `Kernel#pp` / `puts` not left in production code

## Ruby 3.0+

- [ ] Keyword arguments separated from positional arguments (Ruby 3.0 enforces this)
- [ ] Pattern matching (`case`/`in`) used for exhaustive variant dispatch
- [ ] `Ractor` used only for truly isolated compute work — shared mutable state causes `Ractor::IsolationError`
- [ ] `Hash#except` used instead of `reject { |k| k == :foo }` for clarity
- [ ] `Data.define` (Ruby 3.2+) used for immutable value objects instead of `Struct` where mutation is undesired
- [ ] `it` block parameter (Ruby 3.4+) used only in simple single-argument blocks — not nested blocks
- [ ] Numbered block parameters (`_1`, `_2`) used only for very short blocks where naming adds no clarity

## Rails 7+

- [ ] Strong parameters (`params.require().permit()`) applied before mass assignment
- [ ] CSRF protection not disabled (`protect_from_forgery`) on non-API controllers
- [ ] `before_action :authenticate_user!` applied on all controllers that expose private data
- [ ] Database queries use scopes or explicit conditions — not `where("raw_sql #{param}")` string interpolation
- [ ] Background jobs are idempotent — safe to retry on failure
- [ ] `after_commit` used instead of `after_save` for side effects that depend on the transaction completing
- [ ] Asset precompile list includes new CSS/JS files added to the pipeline
- [ ] Zeitwerk autoloading followed — files named in snake_case matching the constant name
- [ ] `counter_cache: true` used on `belongs_to` associations where count queries are frequent
- [ ] `dependent: :destroy` vs `dependent: :delete_all` chosen deliberately — destroy runs callbacks, delete_all does not
- [ ] N+1 queries in controller actions verified absent via `bullet` gem or manual `includes` review
