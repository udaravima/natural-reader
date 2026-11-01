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
