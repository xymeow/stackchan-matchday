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

const DEFAULT_SAMPLE_RATE = 24000
const DEFAULT_VOLUME = 0.5
const DEFAULT_BUFFER_MS = 1500

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
          if (onPlayed && calculatePower) onPlayed(calculatePower(buffer))
        },
        onReady(state) {
          trace(`Ready: ${state}\n`)
          if (state) {
            audio.start()
          } else {
            audio.stop()
          }
        },
        onError: (e) => {
          trace('ERROR: ', e, '\n')
          this.streaming = false
          streamer?.close()
          this.audio?.close()
          this.audio = undefined
          reject(e)
        },
        onDone: () => {
          trace('DONE\n')
          this.streaming = false
          streamer?.close()
          this.audio?.close()
          this.audio = undefined
          onDone?.()
          resolve()
        },
      })
    })
  }
}

export { RemoteTTS }
