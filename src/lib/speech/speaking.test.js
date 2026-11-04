import { describe, it, expect } from 'vitest';
import { speakingOptions } from './speaking';

// What /v1/speech/status says with Whisper here, an admin's Sinhala model offered.
const LOCAL = {
  enabled: true, engine: 'local', heard_by: 'this server', window: { language: 'en' },
  languages: [{ code: 'en', name: 'English' }, { code: 'de', name: 'German' }, { code: 'si', name: 'Sinhala' },
    { code: 'ta', name: 'Tamil' }],
  hears: [{ code: 'si', name: 'Sinhala', model: 'whisper-small-si', label: 'Whisper small, Sinhala (run12)' }],
  default_model: { id: 'small', label: 'Whisper small' },
};
const codes = (options) => options.map((o) => o.code);

describe('speakingOptions: what the Speaking switch lists', () => {
  it('Auto, the Talk language, and each offered language with its model', () => {
    expect(speakingOptions(LOCAL, 'auto', 'en')).toEqual([
      { code: 'auto', label: 'Auto', detail: 'Whisper small' },
      { code: 'en', label: 'English', detail: 'Whisper small' },
      { code: 'si', label: 'Sinhala', detail: 'Whisper small, Sinhala (run12)' },
    ]);
  });

  // Picked from Settings → Chat → Speech language's full list: it still shows, and is still what's sent.
  it('keeps a stored language the list doesn\'t have', () => {
    expect(speakingOptions(LOCAL, 'de', 'en').at(-1)).toEqual({ code: 'de', label: 'German', detail: 'Whisper small' });
  });

  it('with Google, the recognizer\'s languages', () => {
    const google = { enabled: true, engine: 'google', heard_by: 'Google Cloud', window: { language: 'en' },
      languages: [{ code: 'si', name: 'Sinhala' }, { code: 'ta', name: 'Tamil' }, { code: 'en', name: 'English' }],
      hears: [], default_model: null };
    expect(codes(speakingOptions(google, 'auto', 'en'))).toEqual(['auto', 'si', 'ta', 'en']);
    expect(speakingOptions(google, 'auto', 'en')[1].detail).toBe('Google Cloud');
  });

  it('with no status yet, voice off, or no languages, Auto alone', () => {
    expect(codes(speakingOptions(null, 'auto', 'en'))).toEqual(['auto']);
    expect(codes(speakingOptions({ enabled: false }, 'auto', 'en'))).toEqual(['auto']);
    expect(codes(speakingOptions({ enabled: true, engine: 'google', languages: [] }, 'auto', 'en'))).toEqual(['auto']);
  });

  it('never lists a language twice', () => {
    const offeredEnglish = { ...LOCAL, hears: [...LOCAL.hears, { code: 'en', name: 'English', model: 'small.en', label: 'Whisper small, English only' }] };
    const out = speakingOptions(offeredEnglish, 'en', 'en');
    expect(codes(out)).toEqual(['auto', 'en', 'si']);
    expect(out[1].detail).toBe('Whisper small, English only');   // the offered model, not the default
  });
});
