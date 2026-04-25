import React, { useEffect, useState } from 'react';
import { useMCPStore, type MCPServerInfo } from '../core/state/mcpStore';

/* ── Status badge ── */
const StatusDot: React.FC<{ status: string }> = ({ status }) => {
  const color =
    status === 'running'
      ? 'bg-emerald-400 shadow-emerald-400/50'
      : status === 'starting'
      ? 'bg-amber-400 shadow-amber-400/50 animate-pulse'
      : status === 'error'
      ? 'bg-red-400 shadow-red-400/50'
      : 'bg-zinc-500';

  return <span className={`inline-block w-2 h-2 rounded-full shadow-lg ${color}`} />;
};

/* ── Type badge ── */
const TypeBadge: React.FC<{ type: string }> = ({ type }) => {
  const styles: Record<string, string> = {
    builtin: 'bg-indigo-500/15 text-indigo-300 border-indigo-500/20',
    docker: 'bg-sky-500/15 text-sky-300 border-sky-500/20',
    custom: 'bg-violet-500/15 text-violet-300 border-violet-500/20',
  };

  return (
    <span className={`text-[9px] uppercase tracking-wider font-semibold px-2 py-0.5 rounded-full border ${styles[type] || styles.custom}`}>
      {type}
    </span>
  );
};

/* ── Server Card ── */
const ServerCard: React.FC<{
  server: MCPServerInfo;
  onRemove: () => void;
  onRestart: () => void;
  onToggleEnabled?: () => void;
}> = ({ server, onRemove, onRestart, onToggleEnabled }) => {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="group bg-white/[0.02] hover:bg-white/[0.04] border border-white/5 hover:border-white/10 rounded-2xl p-4 transition-all duration-300">
      {/* Header row */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <StatusDot status={server.status} />
          <span className="text-sm font-semibold text-zinc-200 truncate">{server.name}</span>
          <TypeBadge type={server.type} />
        </div>

        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          {/* Expand/collapse tools */}
          <button
            onClick={() => setExpanded(!expanded)}
            className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-white/5 text-zinc-500 hover:text-zinc-300 transition-colors"
            title="Show tools"
          >
            <span className="material-symbols-outlined text-[16px]">
              {expanded ? 'expand_less' : 'expand_more'}
            </span>
          </button>

          {/* Restart (docker only) */}
          {server.type === 'docker' && (
            <button
              onClick={onRestart}
              className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-amber-500/10 text-zinc-500 hover:text-amber-300 transition-colors"
              title="Restart"
            >
              <span className="material-symbols-outlined text-[16px]">refresh</span>
            </button>
          )}

          {/* Enable/Disable */}
          {onToggleEnabled && (
            <button
              onClick={onToggleEnabled}
              className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-white/10 text-zinc-500 hover:text-zinc-200 transition-colors"
              title={server.enabled === false ? 'Enable server' : 'Disable server'}
            >
              <span className="material-symbols-outlined text-[16px]">
                {server.enabled === false ? 'toggle_off' : 'toggle_on'}
              </span>
            </button>
          )}

          {/* Remove (docker + custom only) */}
          {server.type !== 'builtin' && (
            <button
              onClick={onRemove}
              className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-red-500/10 text-zinc-500 hover:text-red-300 transition-colors"
              title="Remove"
            >
              <span className="material-symbols-outlined text-[16px]">delete</span>
            </button>
          )}
        </div>
      </div>

      {/* Description */}
      {server.description && (
        <p className="text-[11px] text-zinc-500 mt-1.5 pl-5 leading-relaxed">{server.description}</p>
      )}

      {/* Tool count pill */}
      <div className="flex items-center gap-2 mt-2 pl-5">
        <span className="text-[10px] text-zinc-500 bg-white/5 px-2 py-0.5 rounded-full">
          {server.tools.length} tool{server.tools.length !== 1 ? 's' : ''}
        </span>
        {server.type === 'docker' && server.command && (
          <span className="text-[10px] text-zinc-600 font-mono truncate max-w-[200px]">
            {server.args?.slice(-1)?.[0] || server.command}
          </span>
        )}
      </div>

      {/* Expanded tools list */}
      {expanded && server.tools.length > 0 && (
        <div className="mt-3 pl-5 flex flex-wrap gap-1.5">
          {server.tools.map((tool) => (
            <span
              key={tool}
              className="text-[10px] font-mono bg-white/5 border border-white/5 text-zinc-400 px-2 py-1 rounded-lg"
            >
              {tool}
            </span>
          ))}
        </div>
      )}
    </div>
  );
};

/* ── Quick-add cards for popular Local MCPs ── */
const POPULAR_MCPS = [
  { name: 'obsidian', type: 'npx', image: '@modelcontextprotocol/server-obsidian', desc: 'Read & write to Obsidian vaults' },
  { name: 'brave', type: 'npx', image: '@modelcontextprotocol/server-brave-search', desc: 'Search the web using Brave API' },
  { name: 'fetch', type: 'npx', image: '@modelcontextprotocol/server-fetch', desc: 'Fetch & read web content' },
  { name: 'github', type: 'npx', image: '@modelcontextprotocol/server-github', desc: 'GitHub repos, PRs, issues' },
  { name: 'postgres', type: 'npx', image: '@modelcontextprotocol/server-postgres', desc: 'Query PostgreSQL' },
  { name: 'puppeteer', type: 'npx', image: '@modelcontextprotocol/server-puppeteer', desc: 'Browser automation' },
];

const QuickAddCard: React.FC<{
  mcp: typeof POPULAR_MCPS[0];
  alreadyAdded: boolean;
  onAdd: (name: string, image: string, type: 'npx' | 'docker') => void;
}> = ({ mcp, alreadyAdded, onAdd }) => (
  <button
    disabled={alreadyAdded}
    onClick={() => onAdd(mcp.name, mcp.image, mcp.type as 'npx' | 'docker')}
    className={`flex flex-col items-start gap-1.5 p-3 rounded-xl border transition-all duration-200 text-left ${
      alreadyAdded
        ? 'bg-emerald-500/5 border-emerald-500/20 opacity-60 cursor-not-allowed'
        : 'bg-white/[0.02] border-white/5 hover:bg-white/[0.05] hover:border-white/10 cursor-pointer'
    }`}
  >
    <div className="flex items-center justify-between w-full">
      <span className="text-xs font-semibold text-zinc-200">{mcp.name}</span>
      {alreadyAdded ? (
        <span className="material-symbols-outlined text-emerald-400 text-[14px]">check_circle</span>
      ) : (
        <span className="material-symbols-outlined text-zinc-500 text-[14px]">add_circle</span>
      )}
    </div>
    <span className="text-[10px] text-zinc-500 leading-tight">{mcp.desc}</span>
    <span className="text-[9px] font-mono text-zinc-600">{mcp.image}</span>
  </button>
);

/* ═════════════════════ MAIN PANEL ═════════════════════ */

export const MCPPanel: React.FC = () => {
  const {
    servers,
    loading,
    error,
    isPanelOpen,
    setPanelOpen,
    fetchServers,
    activeAddTab,
    setActiveAddTab,
    addDockerForm,
    setAddDockerForm,
    addCustomForm,
    setAddCustomForm,
    addDockerServer,
    addCustomServer,
    removeServer,
    restartDockerServer,
    toggleServerEnabled,
    toggleDockerTool,
    resetAddDockerForm,
    resetAddCustomForm,
  } = useMCPStore();

  const [showAddForm, setShowAddForm] = useState(false);

  // Fetch servers when panel opens
  useEffect(() => {
    if (isPanelOpen) fetchServers();
  }, [isPanelOpen]);

  if (!isPanelOpen) return null;

  const serverNames = new Set(servers.map((s) => s.name));

  const handleQuickAdd = async (name: string, image: string, commandType: 'npx' | 'docker') => {
    setAddDockerForm({ name, image, commandType });
    await addDockerServer();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={() => setPanelOpen(false)}
      />

      {/* Panel */}
      <div className="relative w-full max-w-2xl max-h-[85vh] bg-zinc-900/95 border border-white/5 backdrop-blur-xl rounded-3xl shadow-2xl font-manrope text-zinc-300 flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex justify-between items-center px-6 py-5 border-b border-white/5">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-sky-500/15 flex items-center justify-center">
              <span className="material-symbols-outlined text-sky-400 text-lg">hub</span>
            </div>
            <div>
              <h2 className="text-lg font-bold bg-gradient-to-r from-sky-300 to-indigo-300 bg-clip-text text-transparent">
                MCP Servers
              </h2>
              <p className="text-[10px] text-zinc-500 mt-0.5">
                Model Context Protocol — connect tools & data sources
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => fetchServers()}
              disabled={loading}
              className="w-8 h-8 flex items-center justify-center rounded-full bg-white/5 hover:bg-white/10 transition-colors disabled:opacity-40"
              title="Refresh"
            >
              <span className={`material-symbols-outlined text-sm ${loading ? 'animate-spin' : ''}`}>refresh</span>
            </button>
            <button
              onClick={() => setPanelOpen(false)}
              className="w-8 h-8 flex items-center justify-center rounded-full bg-white/5 hover:bg-white/10 transition-colors"
            >
              <span className="material-symbols-outlined text-sm">close</span>
            </button>
          </div>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto custom-scrollbar px-6 py-4 space-y-5">
          {/* Error banner */}
          {error && (
            <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/20 rounded-xl px-4 py-2.5 text-xs text-red-300">
              <span className="material-symbols-outlined text-[14px]">error</span>
              {error}
              <button
                onClick={() => useMCPStore.getState().setError('')}
                className="ml-auto text-red-400 hover:text-red-200"
              >
                <span className="material-symbols-outlined text-[14px]">close</span>
              </button>
            </div>
          )}

          {/* ── Active Servers ── */}
          <section>
            <h3 className="text-xs uppercase tracking-widest text-zinc-500 font-semibold mb-3 flex items-center gap-2">
              <span className="material-symbols-outlined text-[14px]">dns</span>
              Active Servers
              <span className="ml-auto text-[10px] font-normal normal-case tracking-normal text-zinc-600">
                {servers.length} registered
              </span>
            </h3>

            {loading && servers.length === 0 ? (
              <div className="flex items-center justify-center py-8 text-zinc-600 text-sm gap-2">
                <span className="material-symbols-outlined animate-spin text-base">progress_activity</span>
                Loading servers...
              </div>
            ) : servers.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 text-zinc-600 text-sm gap-2">
                <span className="material-symbols-outlined text-3xl text-zinc-700">cloud_off</span>
                <p>No MCP servers connected</p>
                <p className="text-[10px] text-zinc-700">Add a Docker or Custom server below</p>
              </div>
            ) : (
              <div className="space-y-2">
                {servers.map((s) => (
                  <ServerCard
                    key={`${s.type}-${s.name}`}
                    server={s}
                    onRemove={() => removeServer(s.name, s.type)}
                    onRestart={() => restartDockerServer(s.name)}
                    onToggleEnabled={
                      s.type === 'docker'
                        ? () => toggleDockerTool(s.name)
                        : () => toggleServerEnabled(s.name, !(s.enabled ?? true))
                    }
                  />
                ))}
              </div>
            )}
          </section>

          {/* ── Quick Add ── */}
          <section>
            <h3 className="text-xs uppercase tracking-widest text-zinc-500 font-semibold mb-3 flex items-center gap-2">
              <span className="material-symbols-outlined text-[14px]">bolt</span>
              Quick Add — Popular NPX Servers
            </h3>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {POPULAR_MCPS.map((mcp) => (
                <QuickAddCard
                  key={mcp.name}
                  mcp={mcp}
                  alreadyAdded={serverNames.has(mcp.name)}
                  onAdd={handleQuickAdd}
                />
              ))}
            </div>
          </section>

          {/* ── Add Server Form ── */}
          <section>
            <button
              onClick={() => setShowAddForm(!showAddForm)}
              className="w-full flex items-center justify-center gap-2 py-3 rounded-2xl border border-dashed border-white/10 hover:border-white/20 text-zinc-500 hover:text-zinc-300 transition-all text-sm"
            >
              <span className="material-symbols-outlined text-base">{showAddForm ? 'remove' : 'add'}</span>
              {showAddForm ? 'Collapse' : 'Add Custom Server'}
            </button>

            {showAddForm && (
              <div className="mt-3 bg-white/[0.02] border border-white/5 rounded-2xl p-4 space-y-4">
                {/* Tabs */}
                <div className="flex gap-1 bg-zinc-800/50 p-1 rounded-xl">
                  {(['docker', 'custom'] as const).map((tab) => (
                    <button
                      key={tab}
                      onClick={() => setActiveAddTab(tab)}
                      className={`flex-1 py-2 text-xs font-semibold uppercase tracking-wider rounded-lg transition-all ${
                        activeAddTab === tab
                          ? 'bg-white/10 text-white'
                          : 'text-zinc-500 hover:text-zinc-300'
                      }`}
                    >
                      {tab === 'docker' ? '🐳 Local Process' : '🔌 Custom API'}
                    </button>
                  ))}
                </div>

                {/* Local Process Form */}
                {activeAddTab === 'docker' && (
                  <div className="space-y-3">
                    <div className="flex gap-4">
                      <InputField
                        label="Server Name"
                        placeholder="e.g. obsidian"
                        value={addDockerForm.name}
                        onChange={(v) => setAddDockerForm({ name: v })}
                      />
                      <div className="flex flex-col gap-1 w-1/3">
                        <label className="text-[10px] text-zinc-400 pl-1">Runner Type</label>
                        <select 
                          value={addDockerForm.commandType || 'npx'}
                          onChange={(e) => setAddDockerForm({ commandType: e.target.value as 'npx' | 'docker' })}
                          className="w-full bg-zinc-800/50 border border-white/10 rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-sky-500/50 transition-colors"
                        >
                          <option value="npx">NPX (Node.js)</option>
                          <option value="docker">Docker</option>
                        </select>
                      </div>
                    </div>
                    <InputField
                      label={addDockerForm.commandType === 'docker' ? "Docker Image" : "NPM Package Name"}
                      placeholder={addDockerForm.commandType === 'docker' ? "e.g. mcp/time or mcp/obsidian" : "e.g. @modelcontextprotocol/server-obsidian"}
                      value={addDockerForm.image}
                      onChange={(v) => setAddDockerForm({ image: v })}
                    />
                    <TextAreaField
                      label={addDockerForm.commandType === 'docker' ? "Extra Docker Args (one per line)" : "Extra NPX Args (one per line)"}
                      placeholder={addDockerForm.commandType === 'docker' ? '-e OBSIDIAN_API_KEY=your-key\n-v /path/to/vault:/vault' : '--dir\n/path/to/vault\n--port\n8080'}
                      value={addDockerForm.args}
                      onChange={(v) => setAddDockerForm({ args: v })}
                      rows={2}
                    />
                    <TextAreaField
                      label="Environment Variables (KEY=VALUE, one per line)"
                      placeholder={'OBSIDIAN_HOST=host.docker.internal\nOBSIDIAN_API_KEY=sk-...'}
                      value={addDockerForm.env}
                      onChange={(v) => setAddDockerForm({ env: v })}
                      rows={2}
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={addDockerServer}
                        disabled={loading}
                        className="flex-1 py-2.5 bg-sky-500/20 hover:bg-sky-500/30 border border-sky-500/20 text-sky-300 rounded-xl text-xs font-semibold transition-all disabled:opacity-40"
                      >
                        {loading ? 'Adding...' : 'Add Process Server'}
                      </button>
                      <button
                        onClick={resetAddDockerForm}
                        className="px-4 py-2.5 bg-white/5 hover:bg-white/10 rounded-xl text-xs text-zinc-400 transition-colors"
                      >
                        Clear
                      </button>
                    </div>
                  </div>
                )}

                {/* Custom Form */}
                {activeAddTab === 'custom' && (
                  <div className="space-y-3">
                    <InputField
                      label="Server Name"
                      placeholder="e.g. weather-api"
                      value={addCustomForm.name}
                      onChange={(v) => setAddCustomForm({ name: v })}
                    />
                    <InputField
                      label="Base URL"
                      placeholder="https://api.example.com/mcp"
                      value={addCustomForm.base_url}
                      onChange={(v) => setAddCustomForm({ base_url: v })}
                    />
                    <InputField
                      label="Description"
                      placeholder="What does this server do?"
                      value={addCustomForm.description}
                      onChange={(v) => setAddCustomForm({ description: v })}
                    />
                    <InputField
                      label="Tool Names (comma-separated)"
                      placeholder="search, get_data, analyze"
                      value={addCustomForm.tools}
                      onChange={(v) => setAddCustomForm({ tools: v })}
                    />
                    <InputField
                      label="Auth Header (optional)"
                      placeholder="Bearer sk-..."
                      value={addCustomForm.auth_header}
                      onChange={(v) => setAddCustomForm({ auth_header: v })}
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={addCustomServer}
                        disabled={loading}
                        className="flex-1 py-2.5 bg-violet-500/20 hover:bg-violet-500/30 border border-violet-500/20 text-violet-300 rounded-xl text-xs font-semibold transition-all disabled:opacity-40"
                      >
                        {loading ? 'Adding...' : 'Add Custom Server'}
                      </button>
                      <button
                        onClick={resetAddCustomForm}
                        className="px-4 py-2.5 bg-white/5 hover:bg-white/10 rounded-xl text-xs text-zinc-400 transition-colors"
                      >
                        Clear
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
};

/* ── Shared form fields ── */

const InputField: React.FC<{
  label: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
}> = ({ label, placeholder, value, onChange }) => (
  <div className="flex flex-col gap-1">
    <label className="text-[10px] text-zinc-400 pl-1">{label}</label>
    <input
      type="text"
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full bg-zinc-800/50 border border-white/10 rounded-xl px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-sky-500/50 transition-colors"
    />
  </div>
);

const TextAreaField: React.FC<{
  label: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  rows?: number;
}> = ({ label, placeholder, value, onChange, rows = 3 }) => (
  <div className="flex flex-col gap-1">
    <label className="text-[10px] text-zinc-400 pl-1">{label}</label>
    <textarea
      placeholder={placeholder}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      rows={rows}
      className="w-full bg-zinc-800/50 border border-white/10 rounded-xl px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-sky-500/50 transition-colors resize-none font-mono text-[12px]"
    />
  </div>
);
