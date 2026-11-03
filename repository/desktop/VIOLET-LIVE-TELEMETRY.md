# Violet live animation observations

The native renderer selects all fourteen packaged animations from observed
activity. The artwork and 30 Hz compositor are unchanged. These additions are
display observations; polling cannot start capture, playback, model work or USB
recovery.

| Animation | Live producer | Ends when |
| --- | --- | --- |
| Listening | The Assistant's permission-granted microphone capture is running | Capture ends, stops, is muted/suspended, the view closes, or the heartbeat expires |
| Speaking | Browser speech start/resume, or the current Windows SAPI stream reports that it is speaking | Playback pauses, ends, is cancelled, fails, the view closes, or the heartbeat expires |
| Concerned | A fresh connected Violet `interrupted` report from the existing Dot or delegated local-task route | A newer report clears it, its source disconnects, or it becomes stale |
| Reconnecting | MASTER's established-stream recovery is cleaning up, waiting to retry, or attempting reconnection | Cancellation, exhaustion, permanent refusal, deadline expiry, or the first new acknowledged delivery |

Opening a microphone permission dialog, attaching/uploading audio, generating a
reply, selecting a voice and invoking SAPI `Speak` do not establish listening or
speaking. SAPI runtime status establishes engine activity, not independently
audible sound from the selected output device. A generic missing feed, queued
work or deliberate task cancellation does not create Concerned. Initial manual
Connect is distinct from recovery of a previously acknowledged stream.

## Private transient voice observations

The existing authenticated local interface accepts
`POST /api/master/voice-activity` with exactly these fields:

```json
{
  "sessionId": "32 lowercase hexadecimal characters",
  "sequence": 1,
  "observedAt": "UTC ISO timestamp of this observation",
  "listening": false,
  "speaking": "none"
}
```

`speaking` is `none`, `browser_tts` or `windows_sapi`. This is a complete state
observation from one interface document, not a command to any audio device.
Actual activity sends a heartbeat every second. Stop sends the cleared state.
The receiver accepts increasing safe-integer sequences only, bounds document
identities to 64, retains sequence tombstones for 120 seconds and expires active
observations at exactly five seconds. Client event age and monotonic receiver
age both count toward expiry. Reordered, future or already stale observations
cannot renew a lease. A fresh document starts with a new random identity.

Only fixed source labels, activity kinds and observation times enter
`coolerDisplay.voiceActivity` in the native-worker snapshot copy. No audio,
spoken text, prompt, response, voice name, chat title or media path is accepted
by this operation. Voice observations are memory-only and are absent from the
generic MASTER state, persisted snapshots, lifecycle and plugin exports.

## Read-only recovery projection

`coolerDisplay.connectionActivity` is derived under MASTER's existing lock from
its current connection intent, established-stream recovery budget, pending
cleanup and attempt/retry deadlines. It carries the fixed transport source,
phase, current request ID and observation time. It never modifies the recovery
scheduler. The native selector checks the request ID and freshness again.

The preview can show Reconnecting while USB is unavailable. An unplugged panel
cannot receive new animation frames; its output resumes only when the transport
recovers. Existing writer-conflict, pin, permission, cleanup and retry limits
continue to apply.

## Selection and verification

Failure and needs-input retain highest priority. An interruption, recovery,
listening or speaking observation can preempt ordinary model work and consume a
completion reaction without replaying it afterward. A whole snapshot older than
15 seconds suppresses these reactions. Voice and recovery observations require
fresh five-second timestamps; Violet report freshness keeps its existing bound.

Source fixtures exercise event ordering, stops, stream/document replacement,
expiry boundaries, privacy and state priority with fake devices. Native tests
exercise the selector and original compositor. Installed byte checks, genuine
microphone/voice use and actual cooler observation are separate acceptance
stages. Follow `../app/AI-VOICE-ACCEPTANCE.md` for the user-operated audio checks.
