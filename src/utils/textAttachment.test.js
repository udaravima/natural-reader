import { describe, it, expect } from 'vitest';
import {
    TEXT_FILE_MAX_BYTES,
    TEXT_MESSAGE_MAX_BYTES,
    isTextFile,
    readTextFile,
    maskText,
    textToBase64,
} from './textAttachment';

const env = (s) => maskText('.env', s);
const fileOf = (bytes, name = 'a.txt', type = 'text/plain') =>
    new File([bytes], name, { type });

describe('limits', () => {
    it('are the spec values', () => {
        expect(TEXT_FILE_MAX_BYTES).toBe(65536);
        expect(TEXT_MESSAGE_MAX_BYTES).toBe(131072);
    });
});

describe('isTextFile', () => {
    it('accepts the listed extensions, .env names and text/* types', () => {
        expect(isTextFile(fileOf('x', 'notes.MD', ''))).toBe(true);
        expect(isTextFile(fileOf('x', 'run.sh', ''))).toBe(true);
        expect(isTextFile(fileOf('x', '.env.local', ''))).toBe(true);
        expect(isTextFile(fileOf('x', 'prod.env', ''))).toBe(true);
        expect(isTextFile(fileOf('x', 'weird.zzz', 'text/x-thing'))).toBe(true);
    });
    it('refuses everything else', () => {
        expect(isTextFile(fileOf('x', 'a.pdf', 'application/pdf'))).toBe(false);
        expect(isTextFile(fileOf('x', 'a.png', 'image/png'))).toBe(false);
        expect(isTextFile(fileOf('x', 'noext', ''))).toBe(false);
    });
});

describe('readTextFile', () => {
    it('reads UTF-8 text', async () => {
        expect(await readTextFile(fileOf('héllo\n'))).toEqual({ ok: true, text: 'héllo\n' });
    });
    it('refuses bad UTF-8', async () => {
        const r = await readTextFile(fileOf(new Uint8Array([0xff, 0xfe, 0x41]), 'bad.txt'));
        expect(r).toEqual({ ok: false, error: "bad.txt isn't a text file" });
    });
    it('refuses NUL bytes', async () => {
        const r = await readTextFile(fileOf(new Uint8Array([0x41, 0, 0x42]), 'bin.txt'));
        expect(r).toEqual({ ok: false, error: "bin.txt isn't a text file" });
    });
    it('refuses 65 537 bytes but accepts 65 536', async () => {
        const over = await readTextFile(fileOf(new Uint8Array(65537).fill(97), 'big.txt'));
        expect(over).toEqual({ ok: false, error: 'big.txt is over 64 KB' });
        const ok = await readTextFile(fileOf(new Uint8Array(65536).fill(97), 'big.txt'));
        expect(ok.ok).toBe(true);
    });
});

describe('textToBase64', () => {
    it('encodes UTF-8 bytes', () => {
        expect(textToBase64('é')).toBe('w6k=');
        expect(textToBase64('')).toBe('');
    });
    it('handles large text', () => {
        expect(atob(textToBase64('a'.repeat(100000))).length).toBe(100000);
    });
});

describe('maskText, env-style files', () => {
    it('keeps ports', () => {
        expect(env('PORT=8000\n').text).toBe('PORT=8000\n');
    });
    it('keeps booleans and empty values', () => {
        expect(env('DEBUG=true\nEMPTY=\n').text).toBe('DEBUG=true\nEMPTY=\n');
        expect(env('A=Yes\nB=OFF\n').masked).toBe(0);
    });
    it('masks a secret', () => {
        expect(env('API_KEY=sk-abc123xyz\n')).toEqual({ text: 'API_KEY=<masked, 12 chars>\n', masked: 1 });
    });
    it('masks a quoted URL and drops the quotes', () => {
        expect(env('DATABASE_URL="postgres://u:p@h/db?x=1"\n').text)
            .toBe('DATABASE_URL=<masked URL, 23 chars>\n');
    });
    it('keeps CRLF, comments, export and a missing final newline', () => {
        expect(env('export TOKEN=abc\r\n# comment=keep\r\nPORT=1234567').text)
            .toBe('export TOKEN=<masked, 3 chars>\r\n# comment=keep\r\nPORT=<masked, 7 chars>');
    });
    it('splits at the first = only', () => {
        expect(env('K=a=b==\n')).toEqual({ text: 'K=<masked, 5 chars>\n', masked: 1 });
    });
    it('masks an inline comment along with the value', () => {
        expect(env('K=value # note\n').text).toBe('K=<masked, 12 chars>\n');
    });
    it('masks single-quoted values', () => {
        expect(env("K='abc def'\n").text).toBe('K=<masked, 7 chars>\n');
    });
    it('keeps blank lines and order', () => {
        expect(env('A=1\n\nB=secret\n\n').text).toBe('A=1\n\nB=<masked, 6 chars>\n\n');
    });
    it('detects env style by content', () => {
        expect(maskText('settings.txt', 'a=x\nb=2\nc=3\nhello\n').masked).toBe(1);
    });
    it('masks a private key inside a quoted env value once', () => {
        const r = env('K="-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----"\n');
        expect(r.masked).toBe(1);
        expect(r.text).not.toContain('AAAA');
    });
    it('treats a lone key = "value" source line as code, not env', () => {
        expect(maskText('app.py', 'key = "sk-ant-abcdef1234"').text).toBe('key = "<masked, 17 chars>"');
    });
});

describe('maskText, other files', () => {
    it('masks password: lines', () => {
        expect(maskText('notes.md', 'password: hunter2\nuser: bob\n').text)
            .toBe('password: <masked, 7 chars>\nuser: bob\n');
    });
    it('does not eat the next line when the value is empty', () => {
        expect(maskText('n.md', 'password:\nuser: bob\n')).toEqual({ text: 'password:\nuser: bob\n', masked: 0 });
    });
    it('masks quoted JSON values', () => {
        expect(maskText('c.json', '{"api_key": "abc123"}').text).toBe('{"api_key": "<masked, 6 chars>"}');
    });
    it('masks well-known token prefixes', () => {
        const toks = [
            'ghp_' + 'a'.repeat(36),
            'github_pat_' + 'B'.repeat(30),
            'xoxb-1234567890-abcdef',
            'AKIA' + 'A'.repeat(16),
            'nrp_abcdef123456',
        ];
        for (const t of toks) {
            const r = maskText('t.txt', `use ${t} here`);
            expect(r.masked).toBe(1);
            expect(r.text).toBe(`use <masked, ${t.length} chars> here`);
        }
    });
    it('masks JWTs', () => {
        const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.abc_DEF-123';
        expect(maskText('t.txt', `Bearer ${jwt}`).text).toBe(`Bearer <masked, ${jwt.length} chars>`);
    });
    it('masks the password in a URL only', () => {
        expect(maskText('x.txt', 'https://alice:s3cret@example.com/').text)
            .toBe('https://alice:<masked, 6 chars>@example.com/');
    });
    it('leaves URLs without credentials alone', () => {
        expect(maskText('x.txt', 'see https://example.com:8080/a@b').masked).toBe(0);
    });
    it('counts a private key block as one value', () => {
        const r = maskText('id.txt', '-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n');
        expect(r.masked).toBe(1);
        expect(r.text).toMatch(/^<masked, \d+ chars>\n$/);
    });
    it('still masks a secret inside a comment', () => {
        expect(maskText('a.py', '# token: abc123\n').text).toBe('# token: <masked, 6 chars>\n');
    });
    it('counts each value once', () => {
        expect(maskText('t.txt', 'password: ghp_' + 'a'.repeat(36)).masked).toBe(1);
    });
    it('leaves ordinary text untouched', () => {
        const t = '# Title\nNothing secret.\n';
        expect(maskText('readme.md', t)).toEqual({ text: t, masked: 0 });
    });
});

describe('fix round 1: env files by name fail closed', () => {
    it('masks a multi-line quoted value as one value', () => {
        expect(env('CERT="line1secret\nline2secret\nline3secret"\nPORT=8000\n'))
            .toEqual({ text: 'CERT=<masked, 35 chars>\nPORT=8000\n', masked: 1 });
    });
    it('runs an unclosed quote to the end of the file', () => {
        const r = env('A=1\nCERT="line1secret\nline2secret\n');
        expect(r.text).toBe('A=1\nCERT=<masked, 23 chars>\n');
        expect(r.masked).toBe(1);
    });
    it('masks a CR-only or U+2028 value whole', () => {
        for (const s of ['FOO=abcsecret\rBAR=defsecret\n', 'FOO=abc defsecret\n']) {
            const r = env(s);
            expect(r.masked).toBe(1);
            expect(r.text).not.toContain('secret');
        }
    });
    it('masks lines that do not parse as KEY=value', () => {
        expect(env('3SCALE_KEY=abcsecret\n').text).toBe('3SCALE_KEY=<masked, 9 chars>\n');
        expect(env('ARR[0]=abcsecret\n').text).toBe('ARR[0]=<masked, 9 chars>\n');
        expect(env('DB_PASS: hunter2\n').text).toBe('DB_PASS: <masked, 7 chars>\n');
        expect(env('set FOO=hunter2\n').text).toBe('set FOO=<masked, 7 chars>\n');
        expect(env('justsecretwords\n').text).toBe('<masked, 15 chars>\n');
    });
    it('does not fail closed for env style by content only', () => {
        expect(maskText('x.txt', 'a=x\nb=2\nc=3\nhello\n').text).toBe('a=<masked, 1 chars>\nb=2\nc=3\nhello\n');
    });
    it('scans comments with the generic rules', () => {
        expect(env('# OLD_KEY=sk-ant-realsecret12345\n').text).toBe('# OLD_KEY=<masked, 22 chars>\n');
        expect(env('#DB_PASSWORD=hunter2\n').text).toBe('#DB_PASSWORD=<masked, 7 chars>\n');
        expect(env('# comment=keep\n').text).toBe('# comment=keep\n');
    });
});

describe('fix round 1: spaced config files', () => {
    it('masks values in a spaced .ini', () => {
        const r = maskText('config.ini', '[db]\nhost = db.internal\nuser = admin\npass = hunter2\n');
        expect(r.masked).toBe(3);
        expect(r.text).toBe('[db]\nhost = <masked, 11 chars>\nuser = <masked, 5 chars>\npass = <masked, 7 chars>\n');
    });
    it('leaves spaced assignments in source code alone', () => {
        const t = 'a = 1\nname = "x"\nhost = "h"\n';
        expect(maskText('app.py', t)).toEqual({ text: t, masked: 0 });
    });
});

describe('fix round 1: no re-masking, whole values', () => {
    it('counts a PEM under private_key once', () => {
        const pem = '-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----';
        const r = maskText('a.txt', `private_key: ${pem}`);
        expect(r.masked).toBe(1);
        expect(r.text).toBe(`private_key: <masked, ${pem.length} chars>`);
        const j = maskText('sa.json', JSON.stringify({ private_key: `${pem}\n` }));
        expect(j.masked).toBe(1);
        expect(j.text).not.toContain('AAAA');
    });
    it('counts a URL password once under a token-like user', () => {
        const r = maskText('a.txt', 'https://x-access-token:abcd1234@github.com/o/r.git');
        expect(r).toEqual({ text: 'https://x-access-token:<masked, 8 chars>@github.com/o/r.git', masked: 1 });
    });
    it('masks a password whose value is a URL, once', () => {
        const r = maskText('a.txt', 'password: postgres://u:pw@h/db');
        expect(r.masked).toBe(1);
        expect(r.text).not.toContain('pw@');
    });
    it('masks PHP-style arrows', () => {
        expect(maskText('a.php', "'password' => 'hunter2',").text).toBe("'password' => '<masked, 7 chars>',");
    });
    it('masks the whole value', () => {
        expect(maskText('a.txt', 'password: abc,def\n').text).toBe('password: <masked, 7 chars>\n');
        expect(maskText('a.txt', 'x password=abc;def y').text).toBe('x password=<masked, 7 chars> y');
        expect(maskText('a.json', '{"password": "ab\\"cdefgh"}').text).toBe('{"password": "<masked, 10 chars>"}');
        expect(maskText('a.txt', 'password: my pass phrase\n').text).toBe('password: <masked, 14 chars>\n');
    });
    it('masks a Redis URL with an empty user', () => {
        expect(maskText('a.txt', 'redis://:hunter2@redis:6379/0').text).toBe('redis://:<masked, 7 chars>@redis:6379/0');
        expect(maskText('a.txt', 'https://example.com:8080/a@b').masked).toBe(0);
    });
    it('masks PGP blocks and unterminated private keys', () => {
        const pgp = '-----BEGIN PGP PRIVATE KEY BLOCK-----\nxyz\n-----END PGP PRIVATE KEY BLOCK-----';
        expect(maskText('a.txt', pgp)).toEqual({ text: `<masked, ${pgp.length} chars>`, masked: 1 });
        const r = maskText('a.txt', 'intro\n-----BEGIN RSA PRIVATE KEY-----\nMIIE\nmore');
        expect(r.masked).toBe(1);
        expect(r.text).toMatch(/^intro\n<masked, \d+ chars>$/);
    });
    it('masks sk- tokens of 8 characters after the prefix', () => {
        expect(maskText('a.txt', 'use sk-abc123xyz here').text).toBe('use <masked, 12 chars> here');
    });
});

describe('fix round 1: robustness', () => {
    it('stays fast on 64 KB unbroken runs', () => {
        const runs = ['a'.repeat(65536), 'ab12'.repeat(16384), 'a.'.repeat(32768), 'a-'.repeat(32768),
            'a'.repeat(32768) + '://' + 'b'.repeat(32768), ' '.repeat(65536)];
        for (const name of ['x.txt', '.env']) {
            for (const run of runs) {
                const t0 = performance.now();
                maskText(name, run);
                expect(performance.now() - t0).toBeLessThan(500);
            }
        }
    });
    it('returns ok:false when the file cannot be read', async () => {
        const f = { name: 'gone.txt', size: 5, arrayBuffer: () => Promise.reject(new Error('x')) };
        expect(await readTextFile(f)).toEqual({ ok: false, error: "gone.txt couldn't be read" });
    });
});

describe('fix round 2: the key rule scans, it does not swallow the line', () => {
    const x = (s) => maskText('x.txt', s);
    it('finds a later secret on the same line', () => {
        const r = x('docker run -e USE_TOKEN=true -e PASSWORD=hunter2 image');
        expect(r.text).toBe('docker run -e USE_TOKEN=true -e PASSWORD=<masked, 7 chars> image');
        expect(r.masked).toBe(1);
        expect(x('x debug_secret=none password=hunter2').masked).toBe(1);
        expect(x('docker run -e POSTGRES_PASSWORD=abc -e API_KEY=realkey123 image').masked).toBe(2);
        expect(x('curl --password=abc --token=realtoken123 https://h').masked).toBe(2);
    });
    it('handles minified JSON and inline objects', () => {
        expect(x('{"max_tokens": 1024, "api_key": "realkey123"}').masked).toBe(2);
        expect(x('auth: {password: abc, token: realtoken}'))
            .toEqual({ text: 'auth: {password: <masked, 3 chars>, token: <masked, 9 chars>}', masked: 2 });
        expect(x('{"token_count":5,"user":"bob","age":3,"items":[1,2,3]}'))
            .toEqual({ text: '{"token_count":<masked, 1 chars>,"user":"bob","age":3,"items":[1,2,3]}', masked: 1 });
        const r = x('{"token_count": 5, "password": "x"}');
        expect(r.text).toBe('{"token_count": <masked, 1 chars>, "password": "<masked, 1 chars>"}');
        expect(r.masked).toBe(2);
    });
    it('finds URL credentials and later keys together', () => {
        const a = x('git clone https://x-access-token:abcd1234@github.com/o/r.git && password=hunter2');
        expect(a.masked).toBe(2);
        expect(a.text).not.toContain('abcd1234');
        expect(a.text).not.toContain('hunter2');
        const b = x('x https://token:s3cret@h/?password=abc');
        expect(b.masked).toBe(2);
        expect(b.text).not.toContain('s3cret');
        expect(b.text).not.toContain('=abc');
    });
    it('handles comparison and assignment operators', () => {
        expect(x('if password == "hunter2":')).toEqual({ text: 'if password == "<masked, 7 chars>":', masked: 1 });
        expect(x("password === 'hunter2'")).toEqual({ text: "password === '<masked, 7 chars>'", masked: 1 });
        for (const s of ['if password != "hunter2":', 'if password := "hunter2"; ok {']) {
            const r = x(s);
            expect(r.masked).toBe(1);
            expect(r.text).not.toContain('hunter2');
        }
    });
    it('handles a typed default', () => {
        expect(x('def f(password: str = "hunter2"):'))
            .toEqual({ text: 'def f(password: str = "<masked, 7 chars>"):', masked: 1 });
    });
    it('keeps a doubled quote inside the value', () => {
        const r = x("password: 'it''s-real'");
        expect(r.text).not.toContain('s-real');
        expect(r.masked).toBe(1);
    });
    it('keeps the whole value when the line is only key: value', () => {
        expect(x('password: abc,def')).toEqual({ text: 'password: <masked, 7 chars>', masked: 1 });
        expect(x('password=abc;def')).toEqual({ text: 'password=<masked, 7 chars>', masked: 1 });
    });
    it('does not treat :: as an operator', () => {
        expect(x('token::parse()')).toEqual({ text: 'token::parse()', masked: 0 });
    });
});

describe('fix round 2: comments cannot hide a CR-only .env', () => {
    it('masks values after a comment joined by CR or U+2028', () => {
        for (const s of [
            '# prod config\rFOO=abcsecret\rBAR=defsecret\n',
            '\r# note\rBAR=defsecret\r',
            '# prod FOO=abcsecret\n',
        ]) {
            const r = env(s);
            expect(r.text).not.toContain('abcsecret');
            expect(r.text).not.toContain('defsecret');
            expect(r.masked).toBeGreaterThan(0);
        }
    });
});

describe('fix round 2: a placeholder in an env value counts once', () => {
    const pem = '-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----';
    it('counts a quoted PEM with a trailing newline once, with its real length', () => {
        const r = env(`KEY="${pem}\n"\nPORT=1\n`);
        expect(r).toEqual({ text: `KEY=<masked, ${pem.length + 1} chars>\nPORT=1\n`, masked: 1 });
    });
    it('counts an unterminated PEM once', () => {
        const src = '-----BEGIN RSA PRIVATE KEY-----\nAAAA\n';
        const r = env(`KEY="${src}`);
        expect(r.masked).toBe(1);
        expect(r.text).toBe(`KEY=<masked, ${src.length} chars>`);
    });
    it('counts a PEM followed by a comment once', () => {
        const r = env(`KEY=${pem} # prod`);
        expect(r).toEqual({ text: `KEY=<masked, ${pem.length + 7} chars>`, masked: 1 });
    });
    it('counts a PEM inside JSON once', () => {
        const src = pem.replace(/\n/g, '\\n');
        const v = `{"private_key":"${src}"}`;
        const r = env(`CREDS=${v}`);
        expect(r).toEqual({ text: `CREDS=<masked, ${v.length} chars>`, masked: 1 });
    });
    it('leaves a value that is exactly a PEM as one masked value', () => {
        expect(env(`KEY=${pem}\n`)).toEqual({ text: `KEY=<masked, ${pem.length} chars>\n`, masked: 1 });
    });
    it('counts the CR of a CRLF inside a quoted value', () => {
        expect(env('FOO="a\r\nb"\r\n').text).toBe('FOO=<masked, 4 chars>\r\n');
    });
});

describe('fix round 2: timing', () => {
    it('stays fast on 64 KB runs of key words and JWT starts', () => {
        for (const w of ['token', 'token.', 'secret', 'api_key', 'password', 'eyJ-']) {
            const run = w.repeat(Math.ceil(65536 / w.length));
            const t0 = performance.now();
            maskText('x.txt', run);
            expect(performance.now() - t0).toBeLessThan(500);
        }
    });
    it('stays fast on many keywords on one line', () => {
        const run = 'a token b '.repeat(6000);
        const t0 = performance.now();
        maskText('x.txt', run);
        expect(performance.now() - t0).toBeLessThan(500);
    });
});

describe('fix round 3: the URL skip stays linear', () => {
    const timeIt = (name, text) => {
        const t0 = performance.now();
        maskText(name, text);
        return performance.now() - t0;
    };
    it('handles 64 KB runs that contain URL userinfo', () => {
        for (const unit of ['a://token:b@', 'https://token:p@h,', 'https://x-access-token:abcd1234@github.com/o/r.git,']) {
            const run = unit.repeat(Math.ceil(65536 / unit.length));
            expect(timeIt('x.txt', run)).toBeLessThan(500);
        }
    });
    it('scales roughly linearly', () => {
        const unit = 'a://token:b@';
        const small = timeIt('x.txt', unit.repeat(2000)) + 1;
        const big = timeIt('x.txt', unit.repeat(4000)) + 1;
        expect(big / small).toBeLessThan(8);
    });
    it('still leaves userinfo to the URL rule, and finds a later key', () => {
        expect(maskText('x.txt', 'https://token:s3cret@h/?password=abc').masked).toBe(2);
        expect(maskText('x.txt', '//token:abc@h')).toEqual({ text: '//token:<masked, 5 chars>', masked: 1 });
    });
});

describe('fix round 3: placeholders already in the file are ordinary text', () => {
    it('masks a value that merely starts with a placeholder', () => {
        expect(env('API_KEY=<masked, 12 chars> # rotated\n'))
            .toEqual({ text: 'API_KEY=<masked, 28 chars>\n', masked: 1 });
    });
    it('counts two placeholders in one value once', () => {
        expect(env('KEYS=<masked, 3 chars>,<masked, 4 chars>\n').masked).toBe(1);
        expect(env('A=<masked, 3 chars>,<masked, 4 chars>\nB=realsecret\n').masked).toBe(2);
    });
    it('keeps a value that is exactly a placeholder, count 0', () => {
        expect(env('K=<masked, 12 chars>\n')).toEqual({ text: 'K=<masked, 12 chars>\n', masked: 0 });
    });
    it('never goes negative on a long run of placeholders', () => {
        const r = env(`K=${'<masked, 1 chars>'.repeat(3855)}\n`);
        expect(r.masked).toBe(1);
    });
    it('masks look-alikes that are not the real shape', () => {
        const a = env('PASSWORD=<maskedhunter2>\n');
        expect(a.masked).toBe(1);
        expect(a.text).not.toContain('hunter2');
        const b = env('PASSWORD="<masked hunter2>"\n');
        expect(b.masked).toBe(1);
        expect(b.text).not.toContain('hunter2');
        const c = maskText('x.txt', 'password: <maskedhunter2>');
        expect(c.masked).toBe(1);
        expect(c.text).not.toContain('hunter2');
    });
    it('counts a private key in a comment line once', () => {
        const pem = '-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----';
        const r = env(`# old: ${pem}\n`);
        expect(r.masked).toBe(1);
        expect(r.text).not.toContain('AAAA');
    });
});

describe('fix round 3: bracketed values run to their close', () => {
    const x = (s) => maskText('x.txt', s);
    it('masks a whole JSON array or object', () => {
        expect(x('{"tokens":["aaa111","bbb222"]}')).toEqual({ text: '{"tokens":<masked, 19 chars>}', masked: 1 });
        expect(x('{"secrets":{"db":"hunter2","api":"abc123"}}'))
            .toEqual({ text: '{"secrets":<masked, 31 chars>}', masked: 1 });
    });
    it('masks a bracketed unquoted-key value', () => {
        expect(x('x tokens=[aaa111,bbb222] y')).toEqual({ text: 'x tokens=<masked, 15 chars> y', masked: 1 });
    });
    it('is quote-aware inside brackets', () => {
        const r = x('{"tokens":["a]b","ccc333"]}');
        expect(r.masked).toBe(1);
        expect(r.text).not.toContain('ccc333');
    });
    it('takes the rest of the line when the bracket never closes', () => {
        const r = x('{"tokens":["aaa111",');
        expect(r.masked).toBe(1);
        expect(r.text).not.toContain('aaa111');
    });
    it('stays fast on 64 KB runs of opening brackets', () => {
        for (const unit of ['token=[', '"token":{']) {
            const run = unit.repeat(Math.ceil(65536 / unit.length));
            const t0 = performance.now();
            x(run);
            expect(performance.now() - t0).toBeLessThan(500);
        }
    });
});

describe('fix round 3: comment guard covers every line-breaking control', () => {
    it('masks after VT, FF, NEL, RS in a comment line', () => {
        for (const c of ['\v', '\f', '\u0085', '\u001e']) {
            const r = env(`# note${c}FOO=abcsecret\n`);
            expect(r.text).not.toContain('abcsecret');
            expect(r.masked).toBeGreaterThan(0);
        }
    });
    it('keeps a comment with a tab a comment', () => {
        expect(env('# note\tFOO=abcsecret\n')).toEqual({ text: '# note\tFOO=abcsecret\n', masked: 0 });
    });
});
