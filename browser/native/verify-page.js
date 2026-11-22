(async () => {
    const delay = ms => new Promise(r => setTimeout(r, ms));
    const endpoint = location.pathname;
    const options = {cache:"no-store", credentials:"omit", referrerPolicy:"no-referrer"};
    async function fetchText(url, timeout=6000) {
        const response = await fetch(url,{...options,signal:AbortSignal.timeout(timeout)});
        if (!response.ok) throw new Error("HTTP " + response.status);
        return (await response.text()).trim();
    }
    async function report(stage) {
        const result = await fetch(endpoint + stage,{...options,method:"POST",signal:AbortSignal.timeout(3000)});
        if (!result.ok) throw new Error("报告失败");
    }
    const ipify = () => "https://api.ipify.org/?dow=" + crypto.randomUUID();
    async function checkExit() {
        const ip = await fetchText(ipify());
        const trace = await fetchText("https://www.cloudflare.com/cdn-cgi/trace?dow=" + crypto.randomUUID());
        return ip === expected && trace.split("\n").includes("ip=" + expected);
    }
    try {
        if (!await checkExit()) throw new Error("出口不一致");
        // The external relay may be stopped before the final HTTP acknowledgement
        // arrives. The local test server has already recorded the ready message.
        await report("ready").catch(()=>{});
        let down = false;
        for (let i=0;i<75;i++) {
            try { await fetchText(endpoint + "ping",900); }
            catch { down=true;break; }
            await delay(200);
        }
        if (!down) throw new Error("未观察到中转断开");
        let blocked = false;
        try { await fetchText(ipify(),3500); }
        catch (e) { blocked = e instanceof TypeError; }
        for (let i=0;i<55;i++) {
            try { await fetchText(endpoint + "ping",800);break; }
            catch { await delay(600); }
        }
        if (!blocked) throw new Error("出口中断检测未通过");
        await report("blocked");
        if (!await checkExit()) throw new Error("出口未恢复");
        await report("restored");
        window.close();
    } catch (_) {
        document.body.textContent="网络检查未完成";
        for (let i=0;i<45;i++) {
            try { await report("failed"); window.close(); break; } catch { await delay(600); }
        }
    }
})();
