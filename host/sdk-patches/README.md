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

1. **Teardown use-after-free.** `removeTCPCallbacks()` clears the lwIP callback
   argument first (`tcp_arg(pcb, NULL)`) and then the callbacks;
   `tcpReceive`/`tcpSent`/`tcpError` gain NULL-argument guards so any *late*
   delivery observes NULL and bails.
2. **Receive-buffer list race.** `tcp->buffers` was appended to by the lwIP
   task (`tcpReceive` walks to the tail) while the XS task pops and frees
   head nodes in `xs_tcp_read` — with no synchronization, the tail walk can
   traverse a node mid-free. Both sides now do their pointer surgery inside
   `builtinCriticalSection` (heap operations stay outside the critical
   section). `xs_tcp_read`'s length pre-scan and the destructor's buffer drain
   are guarded too.
3. **In-flight `tcpReceive` vs destructor (added 2026-07-15).** The arg-first
   NULL guard only protects callbacks that *start* after the clear. It does
   nothing for a `tcpReceive` already past the guard and mid-tail-walk when the
   XS task runs `xs_tcp_destructor` and frees `tcp->buffers`/`tcp`. Under the
   1.6.0 mod's *concurrent* HTTP handling, connection teardowns overlap inbound
   data far more often than the old serialized handler did, and a
   `tcpReceive` `LoadProhibited` (reached via `tcp_input`) began recurring at
   ~25 min under only light polling. The destructor was changed to clear
   callbacks through the marshaled `tcp_clear_callbacks_safe`.

   **⚠️ Fix 3 is NOT sufficient — the crash still recurs (2026-07-15).** After
   deploying it, the device crashed again at 27 min under real load with a
   healthy heap. Same `tcpReceive` backtrace, but the fault address is a
   *poison* value (`EXCVADDR=0xe2f61b44`) and `tcp` itself is valid: the fault
   is a *buffer-list node* freed while still linked (`tcp->buffers` walking a
   poisoned node), not the `tcp` record. The receive-buffer node lifetime is
   shared across `tcpReceive` (append, tcpip thread), `xs_tcp_read` (consume +
   free, XS thread), and the destructor drain, on two cores; the
   `builtinCriticalSection` guards on the pointer surgery do not cover the
   whole node lifetime. **This is an unresolved SDK-level race that needs the
   maintainer.** The marshaled destructor change is kept as a partial
   hardening but is not claimed to fix the crash.

Soak evidence: the device previously crashed every 15–25 minutes; fixes 1+2
ran clean for 2.1 hours *before* 1.6.0's concurrent HTTP handling, which raised
teardown/receive overlap and reopened the receive-path race. Fix 3 did not
close it. Escalation with the poison-UAF evidence is pending on #1655.

**Known remaining gap (not fixed here).** Listener accept path: a pending
socket (accepted by lwIP, not yet consumed by `xs_listener_read`) has no error
callback — see the `//@@ also install error handler` comment in `listenerAccept`.
If the peer RSTs a pending connection before the XS task consumes it, lwIP frees
the pcb and `pending->skt` dangles; `xs_listener_read` then calls `tcp_err()` on
it and trips `LWIP_ASSERT(... pcb->state != LISTEN)`. Only reproduced under a
pathological burst (10 simultaneous connections RST together), not under match
load. A correct fix installs an error handler on pending sockets and removes
them from the pending list under lock; it needs its own soak and a separate
upstream discussion.

Reported and fixed upstream: issue
[moddable#1655](https://github.com/Moddable-OpenSource/moddable/issues/1655),
PR [moddable#1656](https://github.com/Moddable-OpenSource/moddable/pull/1656).
Once the PR lands (with fix 3), drop this patch and update the SDK instead.
`mcconfig` rebuilds pick the change up automatically; the mod does not need to
be rebuilt.
