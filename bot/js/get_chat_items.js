() => {
    // Ambil elemen chat dari daftar sidebar kiri (maksimal 5 teratas)
    const cells = document.querySelectorAll('[data-cy^="webchat-conversation-cell-root"], li');
    if (cells.length > 0) {
        return Array.from(cells).slice(0, 5); 
    }
    
    // Fallback
    const allDivs = [...document.querySelectorAll('div')];
    const fallbackCells = [];
    for (const div of allDivs) {
        const text = div.textContent || '';
        const hasTimestamp = /\b\d{2}:\d{2}\b/.test(text) || text.includes('Yesterday') || text.includes('Kemarin');
        if (hasTimestamp && text.length > 5 && text.length < 300) {
            const rect = div.getBoundingClientRect();
            if (rect.left > 0 && rect.left < window.innerWidth * 0.4 && rect.height > 20 && rect.height < 150) {
                fallbackCells.push(div);
                if (fallbackCells.length >= 5) break;
            }
        }
    }
    return fallbackCells;
}
