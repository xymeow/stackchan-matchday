# Moddable SDK patches (machine-local)

Unlike `host/patches/` (which apply to the stack-chan checkout), these apply
to the **Moddable SDK** checkout itself (`$MODDABLE`, tested against 8.2.3):

```sh
cd "$MODDABLE"
git apply /path/to/stackchan-matchday/host/sdk-patches/0001-esp32-tcp-clear-callbacks-safe.patch
```

Then rebuild and reflash the host.

## 0001-esp32-tcp-clear-callbacks-safe.patch

Fixes spontaneous `RTC_SW_CPU_RST` reboots caused by a use-after-free race in
the ECMA-419 TCP socket glue: `doClose`/`xs_tcp_destructor` (XS task) cleared
lwIP callbacks with raw `tcp_recv/tcp_sent/tcp_err` calls and then freed the
socket record while the lwIP `tcpip_thread` could still be inside
`tcpReceive` with that record as its argument. Diagnosed from serial-console
panics (`Guru Meditation LoadProhibited` in `tcpReceive`,
`modules/io/socket/lwip/tcp.c`), symbolicated with
`xtensa-esp32s3-elf-addr2line`; 24 such reboots captured in one evening of
ordinary watcher traffic.

The patch fixes two independent races behind the same panic signature
(discussed with the maintainer in
[moddable#1655](https://github.com/Moddable-OpenSource/moddable/issues/1655)):

1. **Teardown use-after-free.** `removeTCPCallbacks()` now clears the lwIP
   callback argument first (`tcp_arg(pcb, NULL)`) and then the callbacks —
   plain pointer stores, per maintainer guidance; no tcpip-thread marshaling
   needed. `tcpReceive`/`tcpSent`/`tcpError` gain NULL-argument guards so any
   late delivery observes NULL and bails. (An earlier revision marshaled the
   clear via `tcpip_api_call`; soak testing showed the simpler arg-first
   variant is sufficient. The unused `tcp_clear_callbacks_safe` helper may
   remain in modLwipSafe from that revision.)
2. **Receive-buffer list race.** `tcp->buffers` was appended to by the lwIP
   task (`tcpReceive` walks to the tail) while the XS task pops and frees
   head nodes in `xs_tcp_read` — with no synchronization, the tail walk can
   traverse a node mid-free. Both sides now do their pointer surgery inside
   `builtinCriticalSection` (heap operations stay outside the critical
   section). This was the direct cause of the `LoadProhibited` panics at the
   `walker->next` walk and kept crashing hosts that had only fix 1.

Soak evidence: the device previously crashed every 15–25 minutes under
ordinary watcher traffic; with both fixes it has run clean for hours
(instrumented via the abort hook so app-level restarts are separable).

Reported and fixed upstream: issue
[moddable#1655](https://github.com/Moddable-OpenSource/moddable/issues/1655),
PR [moddable#1656](https://github.com/Moddable-OpenSource/moddable/pull/1656)
(same two changes). Once the PR lands, drop this patch and update the SDK
instead. `mcconfig` rebuilds pick the change up automatically; the mod does
not need to be rebuilt.
