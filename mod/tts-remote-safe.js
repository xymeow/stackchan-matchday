// Remote-TTS streamer vendored from stack-chan's speeches/tts-remote.ts so the
// audio buffer depth is tunable from the mod. The host pins bufferDuration to
// 600ms; XS-thread busy bursts while speaking (balloon marquee, HTTP handling)
// can exceed that, and every excursion is an audible mid-sentence dropout
// (WavStreamer traces "Ready: false" / "Ready: true" around each one — two per
// long sentence in the 2026-07-14 baseline capture). The TTS server sends a
// complete WAV over the LAN, so a deeper buffer fills in well under realtime
// and start latency is unaffected.
//
// Host audio modules are resolved lazily; on a host without them the
// constructor throws and the caller falls back to the host's own tts-remote.
import Modules from 'modules'
import Timer from 'timer'

const DEFAULT_SAMPLE_RATE = 24000
const DEFAULT_VOLUME = 0.5
const DEFAULT_BUFFER_MS = 1500
// If the stream makes no forward progress — no audio block played, no ready
// transition — for this long, treat it as wedged: tear it down and reject so
// the caller's `finally` clears its busy flag instead of blocking every future
// utterance (observed 2026-07-15: a stalled stream left tts.busy stuck true and
// silenced all commentary until a reboot). The timer is reset on every sign of
// progress, so a long but healthy utterance is never cut off; it only has to be
// generous enough to cover the TTS server's synthesis latency before first byte.
const STALL_TIMEOUT_MS = 15000

class RemoteTTS {
  #AudioOut
  #WavStreamer
  #calculatePower

  constructor(props) {
    this.onPlayed = props.onPlayed
    this.onDone = props.onDone
    this.streaming = false
    this.host = props.host
    this.port = props.port
    this.sampleRate = props.sampleRate ?? DEFAULT_SAMPLE_RATE
    this.volume = props.volume ?? DEFAULT_VOLUME
    this.bufferDuration = props.bufferDuration ?? DEFAULT_BUFFER_MS

    this.#AudioOut = Modules.importNow('pins/audioout')
    this.#WavStreamer = Modules.importNow('wavstreamer')
    if (this.onPlayed) this.#calculatePower = Modules.importNow('calculate-power')
  }

  async stream(key, volume) {
    if (this.streaming) {
      throw new Error('already playing')
    }
    this.streaming = true
    const { onPlayed, onDone } = this
    const calculatePower = this.#calculatePower
    return new Promise((resolve, reject) => {
      // WavStreamer splits the buffer into 8 blocks and each block occupies
      // two queue slots (samples + callback) while it keeps 2 slots free, so
      // the default queueLength of 8 caps the in-flight audio at about half of
      // bufferDuration. 16 slots let the full depth actually queue.
      this.audio = new this.#AudioOut({ streams: 1, sampleRate: this.sampleRate, queueLength: 16 })
      this.audio.enqueue(0, this.#AudioOut.Volume, Math.round((volume ?? this.volume) * 256))
      const audio = this.audio
      let streamer
      let settled = false
      let watchdog

      // Settle exactly once, always releasing the streamer, audio, and busy
      // state first so a wedged stream can never leave `streaming` stuck.
      const settle = (error) => {
        if (settled) return
        settled = true
        if (watchdog !== undefined) {
          Timer.clear(watchdog)
          watchdog = undefined
        }
        this.streaming = false
        try {
          streamer?.close()
        } catch (_closeError) {}
        try {
          this.audio?.close()
        } catch (_closeError) {}
        this.audio = undefined
        if (error) reject(error)
        else {
          onDone?.()
          resolve()
        }
      }
      const kick = () => {
        if (settled) return
        if (watchdog !== undefined) Timer.clear(watchdog)
        watchdog = Timer.set(() => {
          trace('[matchday] tts stream stalled; aborting\n')
          settle(new Error('tts stream stalled'))
        }, STALL_TIMEOUT_MS)
      }
      kick() // arm before the first byte so a dead TTS host is caught too

      streamer = new this.#WavStreamer({
        http: device.network.http,
        host: this.host,
        path: key,
        port: this.port,
        bufferDuration: this.bufferDuration,
        audio: {
          out: audio,
          stream: 0,
        },
        onPlayed(buffer) {
          kick()
          if (onPlayed && calculatePower) onPlayed(calculatePower(buffer))
        },
        onReady(state) {
          kick()
          trace(`Ready: ${state}\n`)
          if (state) {
            audio.start()
          } else {
            audio.stop()
          }
        },
        onError: (e) => {
          trace('ERROR: ', e, '\n')
          settle(e instanceof Error ? e : new Error(String(e)))
        },
        onDone: () => {
          trace('DONE\n')
          settle()
        },
      })
    })
  }
}

export { RemoteTTS }
