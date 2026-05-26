document.addEventListener('DOMContentLoaded', () => {
    const addArchive = document.querySelector('#filePick');
    const chatBox = document.querySelector('.chat-box');

    let archives = [];
    let currentId = null;

    async function openArchive(archiveId) {
        if (!archiveId) return;
        currentId = archiveId;

        const root = window.electronAPI.getUserDataPath();
        const jsonPath = `${root}/archives/${archiveId}/index.json`
        
        let msgs;

        try {
            msgs = await window.electronAPI.readJson(jsonPath);
        } catch (err) {
            console.error('Failed to read cache:', err);
            return;
        }

        renderMessages(msgs);
    }

    function renderMessages(msgs) {
        chatBox.innerHTML = '';
        let lastDate = '';

        msgs.forEach(m => {
            const ts = m.ts ?? new Date(m.timestamp.replace(' ', 'T')).getTime();
            const d = new Date(ts);
            const dateLabel = d.toLocaleDateString();

            if (dateLabel !== lastDate) {
                lastDate = dateLabel;
                const div = document.createElement('div');
                div.className = 'date-divider';
                div.textContent = dateLabel;
                chatBox.appendChild(div);
            }

            chatBox.appendChild(buildBubble(m, ts));
        });

        chatBox.scrollTop = chatBox.scrollHeight;
    }

    function buildBubble(m, ts) {
        const mine = m.sender === 'mmm';
        const wrap = document.createElement('div');
        wrap.className = 'message' + (mine ? ' mine' : '');

        /* header */
        const header = document.createElement('div');
        header.className = 'header';
        header.innerHTML = `
            <img src="./img/profile-circle.png">
            <span class="username">${m.sender}</span>`;
        wrap.appendChild(header);

        /* content */
        const textWrap = document.createElement('div');
        textWrap.className = 'text-wrap';
        const p  = document.createElement('p');
        p.className = 'text';
        p.textContent = m.content;
        const tspan = document.createElement('span');
        tspan.className = 'timestamp';
        tspan.textContent = new Date(ts).toLocaleTimeString([], {
            hour: '2-digit', minute: '2-digit'
        });
        textWrap.append(p, tspan);
        wrap.appendChild(textWrap);

        return wrap;
    }
    
    (async () => {
        archives = await window.archivesAPI.list();
        if (archives.length) openArchive(archives.at(-1).archiveId);
    })();

    /* native picker */
    addArchive.addEventListener('click', async (e) => {
        e.preventDefault();              
        archives = await window.archivesAPI.add(); 
        if (archives.length) openArchive(archives.at(-1).archiveId);
    });

    /* drag and drop folder */
    document.addEventListener('dragover', e => e.preventDefault());
    document.addEventListener('drop', async e => {
        e.preventDefault();

        const f = e.dataTransfer.files[0];
        if (f && f.type === '') {
            archives = await window.archivesAPI.addByPath(f.path);
            console.log('archives >>', archives);
            console.log('last >>', archives?.at?.(-1));

            const last = archives?.at?.(-1);
            if (last) openArchive(last.archiveId)
            openArchive(archives.at(-1).archiveId);
        }
    });
});