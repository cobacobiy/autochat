function isSeller(el, container) {
    let current = el;
    for (let depth = 0; depth < 15; depth++) {
        if (!current) break;
        
        // 1. Periksa data-cy
        const dataCy = current.getAttribute('data-cy') || '';
        if (dataCy.includes('send') || dataCy.includes('seller') || dataCy.includes('to-user')) return true;
        if (dataCy.includes('receive') || dataCy.includes('buyer') || dataCy === 'webchat-message-receive') return false;
        
        // 2. Periksa nama class
        const className = (current.className || '').toString().toLowerCase();
        if (className.includes('send') || className.includes('seller') || 
            className.includes('self') || className.includes('right')) return true;
        if (className.includes('receive') || className.includes('buyer') || className.includes('left')) return false;

        // 3. Periksa CSS alignment (align-self, justify-content, dsb.)
        const style = window.getComputedStyle(current);
        if (style.justifyContent === 'flex-end' || style.textAlign === 'right' || 
            style.alignItems === 'flex-end' || style.flexDirection === 'row-reverse' ||
            style.alignSelf === 'flex-end' || style.justifySelf === 'end') return true;
        
        current = current.parentElement;
    }
    
    // Fallback berdasarkan posisi relatif terhadap kontainer
    if (container && container !== document.body) {
        const cRect = container.getBoundingClientRect();
        const bRect = el.getBoundingClientRect();
        if (cRect.width > 0) {
            const relLeft = (bRect.left - cRect.left) / cRect.width;
            const bubbleCenter = bRect.left + (bRect.width / 2);
            const containerCenter = cRect.left + (cRect.width / 2);
            if (relLeft > 0.4 || bubbleCenter > containerCenter + 10) return true;
            if (bubbleCenter < containerCenter - 10) return false;
        }
    }
    
    // Fallback berdasarkan warna background
    const bubbleStyle = window.getComputedStyle(el);
    const bgColor = bubbleStyle.backgroundColor || '';
    if (bgColor && (
        bgColor.includes('238') ||
        bgColor.includes('255, 87') ||
        bgColor.includes('ee4d2d') ||
        bgColor.includes('232, 245') ||
        bgColor.includes('234, 245') ||
        bgColor.includes('214, 255') ||
        bgColor.includes('204, 255') ||
        el.closest('[class*="seller"]') ||
        el.closest('[class*="right"]') ||
        el.closest('[class*="send"]')
    )) {
        return true;
    }
    
    return false;
}
