// A profile picture, cropped to the centre square and scaled to `size` px,
// as WebP (JPEG where the browser can't encode WebP). A phone photo of a few
// MB comes out at a few tens of KB, well under the server's 256 KB cap.
export async function squareImage(file, size = 256) {
    const bitmap = await createImageBitmap(file);
    const side = Math.min(bitmap.width, bitmap.height);
    const sx = (bitmap.width - side) / 2;
    const sy = (bitmap.height - side) / 2;
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    canvas.getContext('2d').drawImage(bitmap, sx, sy, side, side, 0, 0, size, size);
    bitmap.close?.();
    const encode = (type) => new Promise((resolve) => canvas.toBlob(resolve, type, 0.85));
    const webp = await encode('image/webp');
    // A browser without a WebP encoder hands back PNG (or nothing) instead.
    if (webp && webp.type === 'image/webp') return webp;
    return encode('image/jpeg');
}
