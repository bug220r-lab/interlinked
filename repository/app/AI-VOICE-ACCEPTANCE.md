# Optional native voice acceptance

These are explicit human acceptance checks for a reviewed installed build or an
isolated desktop fixture. Synthetic tests do not record a microphone or establish
audible output. Record the package hashes, Ollama version, selected model digest,
WebView2 version, Windows voice, and observed results outside Git.

1. In **Models**, refresh the intended host. Test `audio_speech` with a known WAV
   and its expected words; test `audio_general` separately with known non-speech
   audio. Each audio check repeats three times. A passing fixture does not establish
   general accuracy. Retain failed trials rather than labeling an advertised
   capability as verified.
2. Open **Assistant**, choose **Record voice**, and decline the native microphone
   prompt once. Confirm that no recording is attached. Try again and explicitly
   allow capture. Finish a short recording; verify that it is attached for review
   and **Audio input purpose** defaults to **Speech**. Send without typed text and
   verify that the selected speech-capable model receives the voice request.
3. Start another recording, then leave the Assistant or hide the window. Verify
   that the microphone indicator stops. Also verify the 60-second capture limit.
4. Attach a known WAV file. Confirm the purpose defaults to **General audio** and
   can be changed to **Speech** for a spoken recording. Verify that unsupported
   capabilities produce an explicit unavailable/error state without downloading
   another model.
5. Select an installed Windows voice and enable **Read replies aloud**. Submit a
   short question, listen to the reply, and use **Mute / interrupt** while it is
   speaking. Verify that speech stops on leaving Assistant and that no remote voice
   service is selected. Repeat after a renderer reload.

Record microphone permission, capture lifetime, returned answer, and actual sound
as separate results. Browser voice enumeration and successful SAPI calls alone do
not prove that the intended audio device played the response.

For a build with live cooler animation telemetry, also observe these boundaries:

- The microphone permission dialog alone leaves Listening inactive. After capture
  starts, Violet listens; Finish, a lost/muted microphone, a suspended audio
  context, hiding/leaving Assistant and the sixty-second limit end the state.
- Speaking begins with actual browser playback or observed SAPI stream activity,
  rather than submission or inference. Check pause/end, Mute / interrupt, a
  replacement reply, and leaving Assistant. An old stream's late end event must
  not stop a newer stream's animation.
- Reloading the interface or losing its heartbeat expires the old voice state
  within five seconds. No recording or playback resumes automatically.
- A fresh genuine Violet interruption report selects Concerned until a newer
  report or expiry. A synthetic report cannot establish live Dot access.
- During an already established cooler stream's bounded recovery, the desktop
  preview selects Reconnecting. Explicit Disconnect stops that state immediately;
  the first acknowledged recovered frame clears it. Do not disturb an internal
  pump/fan connector for this test. An unavailable USB panel cannot show new
  frames until its transport recovers.

Keep source fixtures, observed audio-engine state, actual audible output and
human cooler acceptance separate in the release evidence. The telemetry itself
contains only fixed source labels, state and timestamps, never audio or text.
