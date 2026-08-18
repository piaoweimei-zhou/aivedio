(function(){
    const VERSION = '2026.06.08.1';
    const BASE = '/static/director/js';
    const scripts = [
        BASE + '/i18n-core.js',
        BASE + '/i18n/common.js',
        BASE + '/i18n/studio.js',
        BASE + '/i18n/api-settings.js',
        BASE + '/i18n/canvas.js',
        BASE + '/i18n/smart-canvas.js',
        BASE + '/i18n/comfyui-settings.js',
    ];
    const tags = scripts.map(src => '<script src="' + src + '?v=' + VERSION + '"></script>').join('');
    if(document.readyState === 'loading' && document.currentScript){
        document.write(tags);
        return;
    }
    scripts.reduce((promise, src) => promise.then(() => new Promise((resolve, reject) => {
        const script = document.createElement('script');
        script.src = src + '?v=' + VERSION;
        script.onload = resolve;
        script.onerror = reject;
        document.head.appendChild(script);
    })), Promise.resolve()).then(() => window.StudioI18n?.apply?.()).catch(err => console.error('Failed to load i18n modules', err));
})();
