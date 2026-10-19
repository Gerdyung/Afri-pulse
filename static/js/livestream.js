const startHostBtn = document.getElementById('startHost');
const startViewerBtn = document.getElementById('startViewer');
const roomInput = document.getElementById('room');
const localVideo = document.getElementById('localVideo');
const remoteVideo = document.getElementById('remoteVideo');
const exitStreamBtn = document.getElementById('exitStream');

let pc;
let localStream;

const servers = { iceServers: [{ urls: ['stun:stun.l.google.com:19302'] }] };

const socket = io();
window.liveSocket = socket;

socket.on('connect', () => console.log('socket connected'));

socket.on('offer', async (data) => {
    if (!pc) startViewer();
    try {
        await pc.setRemoteDescription(data);
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        socket.emit('answer', { room: roomInput.value, ...answer });
    } catch (e) { console.error('offer handling error', e); }
});

socket.on('answer', async (data) => {
    try { await pc.setRemoteDescription(data); } catch (e) { console.error(e); }
});

socket.on('candidate', async (data) => {
    try { if (pc && data && data.candidate) await pc.addIceCandidate(data.candidate); } catch (e) { console.warn(e); }
});

async function startHost() {
    const room = roomInput.value || 'default';
    socket.emit('join', { room });

    pc = new RTCPeerConnection(servers);

    pc.onicecandidate = e => {
        if (e.candidate) socket.emit('candidate', { room, candidate: e.candidate });
    };

    pc.ontrack = e => { remoteVideo.srcObject = e.streams[0]; };

    localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
    localVideo.srcObject = localStream;
    localStream.getTracks().forEach(t => pc.addTrack(t, localStream));

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    socket.emit('offer', { room, ...pc.localDescription });
    // update UI
    if (exitStreamBtn) {
        exitStreamBtn.classList.remove('hidden');
        startHostBtn && (startHostBtn.disabled = true);
        startViewerBtn && (startViewerBtn.disabled = true);
    }
}

async function startViewer() {
    const room = roomInput.value || 'default';
    socket.emit('join', { room });

    pc = new RTCPeerConnection(servers);

    pc.onicecandidate = e => { if (e.candidate) socket.emit('candidate', { room, candidate: e.candidate }); };
    pc.ontrack = e => { remoteVideo.srcObject = e.streams[0]; };

    // viewers usually don't send local video
}

startHostBtn && startHostBtn.addEventListener('click', startHost);
startViewerBtn && startViewerBtn.addEventListener('click', startViewer);
if (exitStreamBtn) exitStreamBtn.addEventListener('click', exitStream);

function exitStream() {
    try {
        const room = roomInput.value || 'default';
        // notify server (optional)
        socket.emit('leave', { room });
    } catch (e) { console.warn(e); }

    if (pc) {
        try { pc.getSenders && pc.getSenders().forEach(s => s.track && s.track.stop()); } catch (e) { }
        try { pc.close(); } catch (e) { }
        pc = null;
    }

    if (localStream) {
        try { localStream.getTracks().forEach(t => t.stop()); } catch (e) { }
        localStream = null;
    }

    try { localVideo.srcObject = null; remoteVideo.srcObject = null; } catch (e) { }

    if (exitStreamBtn) {
        exitStreamBtn.classList.add('hidden');
        startHostBtn && (startHostBtn.disabled = false);
        startViewerBtn && (startViewerBtn.disabled = false);
    }
}
