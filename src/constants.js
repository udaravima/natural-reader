// Every voice in voices-v1.0.bin. The first letter of an id is the language it's read in (server/tts_language.py), and
// any voice can read Sinhala or Tamil. A sample sentence is in the voice's own language; Japanese stays in kana,
// because espeak can't read kanji.
const VOICE_SAMPLES = {
    Hindi: 'नमस्ते, यह आवाज़ आपके दस्तावेज़ पढ़ सकती है।',
    Spanish: 'Hola, esta voz puede leer tus documentos en voz alta.',
    French: 'Bonjour, cette voix peut lire vos documents à haute voix.',
    Italian: 'Ciao, questa voce può leggere i tuoi documenti ad alta voce.',
    Portuguese: 'Olá, esta voz pode ler os seus documentos em voz alta.',
    Japanese: 'こんにちは、このこえは ぶんしょうを よみあげます。',
    Chinese: '你好,这个声音可以为你朗读文档。',
};
const VOICE_GROUPS = { af: 'US English', am: 'US English', bf: 'UK English', bm: 'UK English' };

const otherVoice = (id, name, language, gender) => ({
    id, name: `${name} (${language} ${gender})`, group: language, sampleText: VOICE_SAMPLES[language],
});

const ENGLISH_VOICES = [
    { id: 'af_heart', name: 'Heart (US Female)', sampleText: "Hi, I'm Heart! My warm and caring voice makes every story feel like a heartfelt conversation." },
    { id: 'af_bella', name: 'Bella (US Female)', sampleText: "Hello, I'm Bella! My elegant and refined tone brings sophistication to your reading experience." },
    { id: 'af_alloy', name: 'Alloy (US Female)', sampleText: "Hey there, I'm Alloy! My versatile voice adapts smoothly to any content you throw my way." },
    { id: 'af_aoede', name: 'Aoede (US Female)', sampleText: "Greetings, I'm Aoede! Named after the muse, I bring artistic expression to every word I speak." },
    { id: 'af_jessica', name: 'Jessica (US Female)', sampleText: "Hi, I'm Jessica! My friendly and approachable voice makes complex topics feel easy to understand." },
    { id: 'af_kore', name: 'Kore (US Female)', sampleText: "Hello, I'm Kore! My youthful energy brings freshness and vitality to every sentence." },
    { id: 'af_nicole', name: 'Nicole (US Female)', sampleText: "Hi there, I'm Nicole! My clear and professional tone is perfect for focused reading sessions." },
    { id: 'af_nova', name: 'Nova (US Female)', sampleText: "Hey, I'm Nova! My bright and dynamic voice illuminates every chapter like a star." },
    { id: 'af_river', name: 'River (US Female)', sampleText: "Hello, I'm River! My calm and flowing voice carries you smoothly through any narrative." },
    { id: 'af_sarah', name: 'Sarah (US Female)', sampleText: "Hi, I'm Sarah! My natural and sincere voice makes every reading feel authentic and genuine." },
    { id: 'af_sky', name: 'Sky (US Female)', sampleText: "Hey there, I'm Sky! My light and airy voice lifts your imagination to new heights." },
    { id: 'am_michael', name: 'Michael (US Male)', sampleText: "Hi, I'm Michael! My confident and articulate voice commands attention with every word." },
    { id: 'am_adam', name: 'Adam (US Male)', sampleText: "Hello, I'm Adam! My deep and trustworthy voice provides a grounded listening experience." },
    { id: 'am_echo', name: 'Echo (US Male)', sampleText: "Hey, I'm Echo! My resonant voice leaves a lasting impression on everything I narrate." },
    { id: 'am_eric', name: 'Eric (US Male)', sampleText: "Hi there, I'm Eric! My engaging and personable tone keeps listeners captivated throughout." },
    { id: 'am_fenrir', name: 'Fenrir (US Male)', sampleText: "Greetings, I'm Fenrir! My powerful and bold voice brings intensity to dramatic passages." },
    { id: 'am_liam', name: 'Liam (US Male)', sampleText: "Hello, I'm Liam! My warm and relatable voice feels like a trusted companion reading by your side." },
    { id: 'am_onyx', name: 'Onyx (US Male)', sampleText: "Hi, I'm Onyx! My rich and velvety voice adds depth and elegance to any text." },
    { id: 'am_puck', name: 'Puck (US Male)', sampleText: "Hey, I'm Puck! My playful and mischievous tone brings whimsy to every story I tell." },
    { id: 'am_santa', name: 'Santa (US Male)', sampleText: "Ho ho ho, I'm Santa! My jolly voice brings good cheer to everything I read." },
    { id: 'bf_emma', name: 'Emma (UK Female)', sampleText: "Hello, I'm Emma! My refined British accent adds a touch of classic elegance to your reading." },
    { id: 'bf_alice', name: 'Alice (UK Female)', sampleText: "Hi there, I'm Alice! My gentle British voice guides you through stories with poise and grace." },
    { id: 'bf_isabella', name: 'Isabella (UK Female)', sampleText: "Greetings, I'm Isabella! My sophisticated British tone brings timeless charm to every narrative." },
    { id: 'bf_lily', name: 'Lily (UK Female)', sampleText: "Hello, I'm Lily! My sweet and melodic British voice blooms beautifully in every passage." },
    { id: 'bm_daniel', name: 'Daniel (UK Male)', sampleText: "Hi, I'm Daniel! My polished British accent delivers clarity and distinction to your content." },
    { id: 'bm_fable', name: 'Fable (UK Male)', sampleText: "Hello, I'm Fable! My storytelling British voice was born to bring tales to life magically." },
    { id: 'bm_george', name: 'George (UK Male)', sampleText: "Greetings, I'm George! My authoritative British voice lends gravitas to important passages." },
    { id: 'bm_lewis', name: 'Lewis (UK Male)', sampleText: "Hi there, I'm Lewis! My thoughtful British tone contemplates every word with care and precision." },
];

export const KOKORO_VOICES = [
    ...ENGLISH_VOICES.map((voice) => ({ ...voice, group: VOICE_GROUPS[voice.id.slice(0, 2)] })),
    otherVoice('hf_alpha', 'Alpha', 'Hindi', 'Female'),
    otherVoice('hf_beta', 'Beta', 'Hindi', 'Female'),
    otherVoice('hm_omega', 'Omega', 'Hindi', 'Male'),
    otherVoice('hm_psi', 'Psi', 'Hindi', 'Male'),
    otherVoice('ef_dora', 'Dora', 'Spanish', 'Female'),
    otherVoice('em_alex', 'Alex', 'Spanish', 'Male'),
    otherVoice('em_santa', 'Santa', 'Spanish', 'Male'),
    otherVoice('ff_siwis', 'Siwis', 'French', 'Female'),
    otherVoice('if_sara', 'Sara', 'Italian', 'Female'),
    otherVoice('im_nicola', 'Nicola', 'Italian', 'Male'),
    otherVoice('pf_dora', 'Dora', 'Portuguese', 'Female'),
    otherVoice('pm_alex', 'Alex', 'Portuguese', 'Male'),
    otherVoice('pm_santa', 'Santa', 'Portuguese', 'Male'),
    otherVoice('jf_alpha', 'Alpha', 'Japanese', 'Female'),
    otherVoice('jf_gongitsune', 'Gongitsune', 'Japanese', 'Female'),
    otherVoice('jf_nezumi', 'Nezumi', 'Japanese', 'Female'),
    otherVoice('jf_tebukuro', 'Tebukuro', 'Japanese', 'Female'),
    otherVoice('jm_kumo', 'Kumo', 'Japanese', 'Male'),
    otherVoice('zf_xiaobei', 'Xiaobei', 'Chinese', 'Female'),
    otherVoice('zf_xiaoni', 'Xiaoni', 'Chinese', 'Female'),
    otherVoice('zf_xiaoxiao', 'Xiaoxiao', 'Chinese', 'Female'),
    otherVoice('zf_xiaoyi', 'Xiaoyi', 'Chinese', 'Female'),
];

// How many sentences make up one pseudo-page when reading a plain text (.txt) file
export const SENTENCES_PER_TEXT_PAGE = 40;

// Threshold above which .txt segmentation is sliced across animation frames
// to avoid blocking the main thread on very large files
export const TEXT_SEGMENTATION_CHUNK_THRESHOLD = 200_000;

// Keyboard shortcuts config
export const SHORTCUTS = {
    PLAY_PAUSE: ' ',
    STOP: 'Escape',
    PREV_SENTENCE: 'ArrowLeft',
    NEXT_SENTENCE: 'ArrowRight',
    PREV_PAGE: 'PageUp',
    NEXT_PAGE: 'PageDown',
    ZOOM_IN: '+',
    ZOOM_OUT: '-',
    TOGGLE_DARK: 'd',
    TOGGLE_DISTRACTION_FREE: 'f',
};
