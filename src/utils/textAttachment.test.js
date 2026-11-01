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
