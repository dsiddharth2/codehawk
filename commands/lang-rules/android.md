# Android Review Rules

## All Versions

- [ ] No `Context` stored in static fields or singletons (memory leak)
- [ ] `AsyncTask` not used — deprecated; use Kotlin coroutines or `WorkManager`
- [ ] `SharedPreferences` not used for sensitive data — use `EncryptedSharedPreferences` or Keystore
- [ ] `WebView` does not enable `setJavaScriptEnabled(true)` unless explicitly required and documented
- [ ] `android:debuggable="true"` not set in production manifests
- [ ] Permissions declared in the manifest are the minimum required — no over-broad permissions
- [ ] `BroadcastReceiver` unregistered in `onStop` / `onDestroy` if registered in `onStart` / `onCreate`
- [ ] `Cursor` objects closed after use to avoid resource leaks
- [ ] No network calls on the main thread (causes `NetworkOnMainThreadException`)
- [ ] `Activity` / `Fragment` references not held in long-lived objects (memory leak)
- [ ] `Intent` extras validated before use — `getStringExtra()` returns null if the key is absent
- [ ] `PendingIntent` flags include `FLAG_IMMUTABLE` (required on API 31+)

## API 26+

- [ ] `NotificationChannel` created before posting notifications (required API 26+)
- [ ] Background service uses `JobIntentService` or `WorkManager`, not a plain `Service`
- [ ] `JobScheduler` / `WorkManager` used for deferred background work instead of AlarmManager
- [ ] Auto-fill hints (`android:autofillHints`) set on form fields for password managers

## Jetpack Compose

- [ ] Composable functions are stateless where possible — state hoisted to parent
- [ ] `remember` / `rememberSaveable` used to survive recompositions and process death respectively
- [ ] `LaunchedEffect` dependencies are correct — all values read inside must be in the key list
- [ ] `SideEffect` / `DisposableEffect` used for non-Compose side effects with cleanup
- [ ] `derivedStateOf` used when derived state computation is expensive and inputs change frequently
- [ ] `Modifier` parameter present and used as the first modifier in composable public APIs
- [ ] `CompositionLocal` not used for data that could be passed explicitly through parameters

## Kotlin-first

- [ ] Kotlin coroutines use structured concurrency — no `GlobalScope.launch`
- [ ] `viewModelScope` used inside `ViewModel`, `lifecycleScope` inside `Activity`/`Fragment`
- [ ] Flow collected with `repeatOnLifecycle(Lifecycle.State.STARTED)` to avoid collecting in background
- [ ] `StateFlow` / `SharedFlow` used instead of `LiveData` in new Kotlin-first code
- [ ] `hiltViewModel()` used for Hilt-injected ViewModels in Compose — not manual `ViewModelProvider`
- [ ] `NavController` navigation not called from inside `LaunchedEffect` with a key that re-fires on recomposition
- [ ] Room database accessed only on background threads — not on `Dispatchers.Main`
