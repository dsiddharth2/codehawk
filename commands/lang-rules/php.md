# PHP Review Rules

## All Versions

- [ ] No hardcoded passwords, API keys, or database credentials in source files
- [ ] User input never interpolated directly into SQL — use PDO prepared statements with `?` placeholders
- [ ] `htmlspecialchars()` or template auto-escaping applied before outputting user data to HTML
- [ ] `eval()` not called with user-controlled strings
- [ ] File inclusion (`include`, `require`) paths not derived from user input (local file inclusion)
- [ ] `$_GET`, `$_POST`, `$_COOKIE`, `$_REQUEST` values never used unvalidated in SQL/shell/file paths
- [ ] Passwords stored with `password_hash()` (bcrypt/argon2) — not MD5, SHA1, or plain text
- [ ] Error display (`display_errors`) disabled in production — errors logged, not rendered
- [ ] `session_regenerate_id(true)` called after authentication to prevent session fixation
- [ ] File uploads validate extension and MIME type server-side — not just client-side
- [ ] Type declarations present on all function parameters and return types (strict_types=1)
- [ ] `declare(strict_types=1)` at the top of all PHP files
- [ ] Exceptions not swallowed silently — caught exceptions logged or re-thrown

## PHP 8.0+

- [ ] Named arguments used for clarity on functions with many optional parameters
- [ ] `match` expressions used instead of `switch` where a value is returned (no fall-through)
- [ ] `nullsafe` operator (`?->`) used for chained method calls on nullable objects
- [ ] Union types (`int|string`) used in signatures instead of doc-block only annotations
- [ ] Attributes (`#[Attribute]`) used instead of docblock annotations where a library supports both

## Laravel 10+

- [ ] Form requests validate input in `rules()` before it reaches the controller
- [ ] `Policy` or `Gate` used for authorization — not manual `if ($user->role == "admin")` checks
- [ ] Eloquent mass assignment protected by `$fillable` or `$guarded` on all models
- [ ] Queue jobs implement `ShouldBeUnique` or are idempotent for safe retry
- [ ] Environment-specific config loaded from `.env` — not hardcoded in `config/` files
- [ ] Migrations are reversible (`down()` method matches `up()` rollback)

## Symfony 6+

- [ ] Services are autowired — no manual `new` inside service constructors
- [ ] Voters used for authorization logic instead of inline `isGranted` string-based checks
- [ ] Twig templates use `{{ variable }}` (auto-escaped) not `{{ variable|raw }}` for user data
- [ ] Event subscribers registered via `#[AsEventListener]` or `getSubscribedEvents()` consistently
- [ ] Messenger handlers are idempotent — safe to retry on failure
- [ ] Cache warmer implemented for expensive service initialization done at deployment time
