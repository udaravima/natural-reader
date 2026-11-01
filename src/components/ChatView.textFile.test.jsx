import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import ChatView from './ChatView';
import AttachmentPreview from './AttachmentPreview';
import { textToBase64 } from '../utils/textAttachment';

const theme = {
    bg: '', bgSecondary: '', bgTertiary: '', border: '', borderSecondary: '',
    text: '', textSecondary: '', textMuted: '', hover: '',
};

const MODELS = [
    { id: 'ollama:gemma4:e4b', name: 'gemma4:e4b', local: true },
    { id: 'ollama:gemma4:31b-cloud', name: 'gemma4:31b-cloud', local: false },
];

const baseProps = (over = {}) => ({
    theme, darkMode: false, effectiveIsMobile: false,
    messages: [], isStreaming: false, selectedModel: 'ollama:gemma4:e4b', reachable: true,
    availableModels: MODELS,
    sendMessage: vi.fn(async () => ({ sent: true })), stopStream: vi.fn(),
    speakingMessageId: null, speakMessage: vi.fn(), stopSpeaking: vi.fn(),
    downloadingMessageId: null, downloadMessageAudio: vi.fn(),
    showToast: vi.fn(), pins: [], onRemovePin: vi.fn(), numCtx: null,
    draft: '', setDraft: vi.fn(), ...over,
});

// A controlling parent, like App: the composer's attachments live outside ChatView.
function Harness({ props, initialModel }) {
    const [pending, setPending] = useState([]);
    const [model, setModel] = useState(initialModel || props.selectedModel);
    return (
        <>
            <ChatView {...props} selectedModel={model} pendingAttachments={pending} setPendingAttachments={setPending} />
            <button onClick={() => setModel('ollama:gemma4:31b-cloud')}>switch-to-cloud</button>
        </>
    );
}

const file = (content, name, type = '') => new File([content], name, { type });
const pick = (files) => {
    const input = document.querySelector('input[type="file"]');
    fireEvent.change(input, { target: { files } });
};
const staged = async (name) => screen.findByRole('button', { name: new RegExp(name.replace('.', '\\.')) });

describe('text files in the message box', () => {
    it('names the paperclip for images and text, and the input accepts text', () => {
        render(<ChatView {...baseProps()} />);
        expect(screen.getByTitle('Attach an image or a text file')).toBeInTheDocument();
        expect(document.querySelector('input[type="file"]').getAttribute('accept')).toContain('.env');
    });

    it('stages a .env as a chip with its size and 1 value masked; Send posts masked text', async () => {
        const props = baseProps({ draft: 'what is this?' });
        render(<Harness props={props} />);
        pick([file('A=secret\n', '.env')]);          // empty MIME type, the real-world .env case
        const chip = await staged('.env');
        expect(chip).toHaveTextContent('9 B');
        expect(chip).toHaveTextContent('1 value masked');
        fireEvent.click(screen.getByTitle('Send (Enter)'));
        const atts = props.sendMessage.mock.calls[0][1];
        expect(atts).toHaveLength(1);
        expect(atts[0]).toMatchObject({ kind: 'text', name: '.env', masked: 1, sendReal: false, maskedText: 'A=<masked, 6 chars>\n' });
        expect(textToBase64(atts[0].maskedText)).toBe(textToBase64('A=<masked, 6 chars>\n'));
    });

    it('opens the viewer on the chip, showing exactly the masked text, and Escape closes it', async () => {
        render(<Harness props={baseProps()} />);
        pick([file('A=secret\n', '.env')]);
        fireEvent.click(await staged('.env'));
        const dlg = screen.getByRole('dialog');
        expect(within(dlg).getByText('A=<masked, 6 chars>').tagName).toBe('PRE');
        expect(within(dlg).queryByText(/secret/)).toBeNull();
        fireEvent.keyDown(dlg, { key: 'Escape' });
        expect(screen.queryByRole('dialog')).toBeNull();
    });

    it('Send real values asks, naming a local model, and confirming stages the original', async () => {
        const props = baseProps({ draft: 'q' });
        render(<Harness props={props} />);
        pick([file('A=secret\n', '.env')]);
        fireEvent.click(await staged('.env'));
        fireEvent.click(screen.getByRole('button', { name: 'Send real values' }));
        expect(screen.getByText('The real values will be sent to gemma4:e4b and saved in this chat.')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Yes, send real values' }));
        expect(screen.queryByRole('dialog')).toBeNull();
        const chip = await staged('.env');
        expect(chip).not.toHaveTextContent('masked');
        fireEvent.click(screen.getByTitle('Send (Enter)'));
        const att = props.sendMessage.mock.calls[0][1][0];
        expect(att.sendReal).toBe(true);
        expect(att.originalText).toBe('A=secret\n');
    });

    it('names the cloud service when the model entry says local:false', async () => {
        render(<Harness props={baseProps({ selectedModel: 'ollama:gemma4:31b-cloud' })} />);
        pick([file('A=secret\n', '.env')]);
        fireEvent.click(await staged('.env'));
        fireEvent.click(screen.getByRole('button', { name: 'Send real values' }));
        expect(screen.getByText('The real values will be sent to gemma4:31b-cloud, which runs on a cloud service, and saved in this chat.')).toBeInTheDocument();
    });

    it('falls back to the id and the plain sentence when the model has no entry', async () => {
        render(<Harness props={baseProps({ selectedModel: 'ollama:mystery', availableModels: [] })} />);
        pick([file('A=secret\n', '.env')]);
        fireEvent.click(await staged('.env'));
        fireEvent.click(screen.getByRole('button', { name: 'Send real values' }));
        expect(screen.getByText('The real values will be sent to ollama:mystery and saved in this chat.')).toBeInTheDocument();
    });

    it('Escape while confirming leaves the chip masked and the next open unconfirmed', async () => {
        render(<Harness props={baseProps()} />);
        pick([file('A=secret\n', '.env')]);
        fireEvent.click(await staged('.env'));
        fireEvent.click(screen.getByRole('button', { name: 'Send real values' }));
        fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
        expect(screen.queryByRole('dialog')).toBeNull();
        expect(await staged('.env')).toHaveTextContent('1 value masked');
        fireEvent.click(await staged('.env'));
        expect(screen.queryByText(/The real values will be sent/)).toBeNull();
        expect(screen.getByRole('button', { name: 'Send real values' })).toBeInTheDocument();
    });

    it('shows no Send real values for a file with nothing masked, and no masked text', async () => {
        render(<Harness props={baseProps()} />);
        pick([file('hello world\n', 'note.txt', 'text/plain')]);
        const chip = await staged('note.txt');
        expect(chip).not.toHaveTextContent('masked');
        fireEvent.click(chip);
        expect(screen.queryByRole('button', { name: 'Send real values' })).toBeNull();
    });

    it('says 2 values masked for two secrets', async () => {
        render(<Harness props={baseProps()} />);
        pick([file('A=secret\nB=hunter22\n', '.env')]);
        expect(await staged('.env')).toHaveTextContent('2 values masked');
    });

    it('refuses a PDF with the Library toast', async () => {
        const props = baseProps();
        render(<Harness props={props} />);
        pick([file('%PDF', 'a.pdf', 'application/pdf')]);
        await waitFor(() => expect(props.showToast).toHaveBeenCalledWith(
            'Only images and text files can be attached here. PDFs go in the Library.', expect.anything()));
        expect(screen.queryByRole('button', { name: /a\.pdf/ })).toBeNull();
    });

    it('refuses a 65 537-byte .txt', async () => {
        const props = baseProps();
        render(<Harness props={props} />);
        pick([file('a'.repeat(65537), 'big.txt', 'text/plain')]);
        await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('big.txt is over 64 KB', expect.anything()));
    });

    it('R3: refuses a dense .env whose masked form would exceed 64 KB', async () => {
        const props = baseProps();
        render(<Harness props={props} />);
        pick([file('A=x\n'.repeat(10000), '.env')]);      // 40 000 bytes, masks to far more
        await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('.env is over 64 KB', expect.anything()));
        expect(screen.queryByRole('button', { name: /\.env/ })).toBeNull();
    });

    it('refuses the file that takes the message over 128 KB, counting earlier files in the same drop', async () => {
        const props = baseProps();
        render(<Harness props={props} />);
        pick([
            file('a'.repeat(65536), 'one.txt', 'text/plain'),
            file('b'.repeat(65536), 'two.txt', 'text/plain'),
            file('c', 'three.txt', 'text/plain'),
        ]);
        await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('The attached text is over 128 KB', expect.anything()));
        expect(await staged('one.txt')).toBeInTheDocument();
        expect(await staged('two.txt')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /three\.txt/ })).toBeNull();
    });

    it('counts text already staged against a later pick', async () => {
        const props = baseProps();
        render(<Harness props={props} />);
        pick([file('a'.repeat(65536), 'one.txt', 'text/plain'), file('b'.repeat(65536), 'two.txt', 'text/plain')]);
        await staged('two.txt');
        pick([file('c', 'three.txt', 'text/plain')]);
        await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('The attached text is over 128 KB', expect.anything()));
    });

    it('R4: switching the model turns masking back on, with a toast', async () => {
        const props = baseProps();
        render(<Harness props={props} />);
        pick([file('A=secret\n', '.env')]);
        fireEvent.click(await staged('.env'));
        fireEvent.click(screen.getByRole('button', { name: 'Send real values' }));
        fireEvent.click(screen.getByRole('button', { name: 'Yes, send real values' }));
        expect(await staged('.env')).not.toHaveTextContent('masked');
        fireEvent.click(screen.getByText('switch-to-cloud'));
        await waitFor(() => expect(props.showToast).toHaveBeenCalledWith('Masking is back on: you changed the model.', expect.anything()));
        expect(await staged('.env')).toHaveTextContent('1 value masked');
    });

    it('R4: switching the model says nothing when nothing was unmasked', async () => {
        const props = baseProps();
        render(<Harness props={props} />);
        pick([file('A=secret\n', '.env')]);
        await staged('.env');
        fireEvent.click(screen.getByText('switch-to-cloud'));
        expect(props.showToast).not.toHaveBeenCalledWith('Masking is back on: you changed the model.', expect.anything());
    });

    it('R5: pasting a file stages it; pasting plain text stays in the box', async () => {
        const props = baseProps();
        render(<Harness props={props} />);
        const box = screen.getByRole('textbox');
        const f = file('A=secret\n', '.env');
        const fileEvt = { clipboardData: { items: [{ kind: 'file', type: '', getAsFile: () => f }] } };
        fireEvent.paste(box, fileEvt);
        expect(await staged('.env')).toBeInTheDocument();
        const textEvt = new Event('paste', { bubbles: true, cancelable: true });
        textEvt.clipboardData = { items: [{ kind: 'string', type: 'text/plain', getAsFile: () => null }] };
        box.dispatchEvent(textEvt);
        expect(textEvt.defaultPrevented).toBe(false);
    });

    it('refuses an unsupported pasted file with the same toast', async () => {
        const props = baseProps();
        render(<Harness props={props} />);
        const f = file('%PDF', 'a.pdf', 'application/pdf');
        fireEvent.paste(screen.getByRole('textbox'), { clipboardData: { items: [{ kind: 'file', type: 'application/pdf', getAsFile: () => f }] } });
        await waitFor(() => expect(props.showToast).toHaveBeenCalledWith(
            'Only images and text files can be attached here. PDFs go in the Library.', expect.anything()));
    });

    it('keeps the full staged attachment when a send is refused', async () => {
        const props = baseProps({ draft: 'q', sendMessage: vi.fn(async () => ({ sent: false, refused: true })) });
        render(<Harness props={props} />);
        pick([file('A=secret\n', '.env')]);
        await staged('.env');
        fireEvent.click(screen.getByTitle('Send (Enter)'));
        expect(await staged('.env')).toHaveTextContent('1 value masked');
        expect(props.sendMessage.mock.calls[0][1][0].originalText).toBe('A=secret\n');
    });
});

describe('a sent text attachment', () => {
    it('renders a chip with name, size and masked, not the text and not the data-gone notice', () => {
        const att = { id: 't1', kind: 'text', name: '.env', mimeType: 'text/plain', size: 20, masked: true };
        const { container } = render(<AttachmentPreview attachment={att} theme={theme} darkMode={false} />);
        expect(container).toHaveTextContent('.env');
        expect(container).toHaveTextContent('20 B');
        expect(container).toHaveTextContent('masked');
        expect(container.querySelector('[title]')?.getAttribute('title') || '').not.toMatch(/not stored/);
    });

    it('shows in a user bubble without the file text', () => {
        const msgs = [{ role: 'user', content: 'what is this?', id: 'u1', attachments: [{ id: 't1', kind: 'text', name: '.env', mimeType: 'text/plain', size: 20, masked: true }] }];
        render(<ChatView {...baseProps({ messages: msgs })} />);
        expect(screen.getByText('.env')).toBeInTheDocument();
        expect(screen.getByText('masked')).toBeInTheDocument();
        expect(screen.queryByText(/<masked/)).toBeNull();
    });

    it('shows no masked word when the real values were sent', () => {
        const att = { id: 't1', kind: 'text', name: 'a.txt', mimeType: 'text/plain', size: 5, masked: false };
        const { container } = render(<AttachmentPreview attachment={att} theme={theme} darkMode={false} />);
        expect(container).not.toHaveTextContent('masked');
    });
});
