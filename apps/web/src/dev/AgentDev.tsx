import { useEffect, useState } from 'react';
import { AgentConsultView } from '@/components/AgentConsultView';
import { useAgentChat } from '@/components/agent/useAgentChat';
import { triggerHaptic } from '@/lib/fluid';
import { readSiteLanguage } from '@/lib/site-language';
import './agent.css';

/**
 * 개발용 상담 화면(/agent-dev). 실제 AI 상담 탭(AgentConsultView)을 그대로 띄워,
 * 화면이 바뀌면 같이 바뀌고 에이전트 응답 변화만 따로 확인할 수 있게 한다.
 * 언어는 앱 설정을 따르며, ?lang=ko 또는 ?lang=ja로 바꿀 수 있다.
 */
export default function AgentDev() {
  const [lang] = useState(() => readSiteLanguage(new URLSearchParams(location.search).get('lang')));
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  const chat = useAgentChat(lang);

  // 앱과 같은 틀(app-shell > app-col > screen)에 넣어 폭과 배치를 맞춘다.
  return <div className="app-shell">
    <div className="app-col agent-dev">
      <main className="screen">
        <AgentConsultView
          lang={lang}
          chat={chat}
          onOpenPriceTable={() => { location.href = '/?lang=' + lang; }}
          onHaptic={() => triggerHaptic(6)}
        />
      </main>
    </div>
  </div>;
}
