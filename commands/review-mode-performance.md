# Performance Review Checklist

## Database
- [ ] No N+1 queries — use .Include() or projection
- [ ] Bulk operations use ExecuteUpdate/ExecuteDelete (not loop + SaveChanges)
- [ ] Read-only queries use .AsNoTracking()
- [ ] Pagination present on list endpoints

## I/O
- [ ] No synchronous I/O on hot paths (use async/await)
- [ ] HTTP calls use HttpClientFactory, not new HttpClient()
- [ ] File operations buffered or streamed for large payloads

## Collections + Algorithms
- [ ] No O(n^2) loops on unbounded collections
- [ ] LINQ queries materialized once (.ToList()) not re-enumerated
- [ ] Large read-only collections use FrozenSet/FrozenDictionary (.NET 8+)

## Caching
- [ ] Frequently-read, rarely-changed data cached appropriately
- [ ] Cache keys include all discriminating parameters
- [ ] Cache expiry set (no indefinite caching)
