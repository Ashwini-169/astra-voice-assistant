import { create } from 'zustand';

// ── Types ──

export interface MCPTool {
  server: string;
  tool: string;
  description: string;
}

export interface MCPServerInfo {
  name: string;
  type: 'builtin' | 'docker' | 'custom';
  status: string;
  tools: string[];
  description?: string;
  // Docker-specific
  command?: string;
  args?: string[];
  // Custom-specific
  base_url?: string;
  enabled?: boolean;
}

export interface MCPAddDockerForm {
  name: string;
  commandType?: 'docker' | 'npx';
  image: string;
  args: string;
  env: string;
}

export interface MCPAddCustomForm {
  name: string;
  base_url: string;
  description: string;
  tools: string;
  auth_header: string;
}

interface MCPStore {
  // Data
  servers: MCPServerInfo[];
  loading: boolean;
  error: string;
  isPanelOpen: boolean;

  // Add-server forms
  addDockerForm: MCPAddDockerForm;
  addCustomForm: MCPAddCustomForm;
  activeAddTab: 'docker' | 'custom';

  // Actions
  setPanelOpen: (open: boolean) => void;
  setServers: (servers: MCPServerInfo[]) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string) => void;
  setActiveAddTab: (tab: 'docker' | 'custom') => void;
  setAddDockerForm: (form: Partial<MCPAddDockerForm>) => void;
  setAddCustomForm: (form: Partial<MCPAddCustomForm>) => void;
  resetAddDockerForm: () => void;
  resetAddCustomForm: () => void;

  // API actions
  fetchServers: () => Promise<void>;
  addDockerServer: () => Promise<void>;
  addCustomServer: () => Promise<void>;
  removeServer: (name: string, type: string) => Promise<void>;
  restartDockerServer: (name: string) => Promise<void>;
  toggleServerEnabled: (name: string, enabled: boolean) => Promise<void>;
  toggleDockerTool: (name: string) => Promise<void>;
}

const LLM_BASE = 'http://127.0.0.1:8002';

const defaultDockerForm: MCPAddDockerForm = {
  name: '',
  commandType: 'npx',
  image: '',
  args: '',
  env: '',
};

const defaultCustomForm: MCPAddCustomForm = {
  name: '',
  base_url: '',
  description: '',
  tools: '',
  auth_header: '',
};

export const useMCPStore = create<MCPStore>((set, get) => ({
  servers: [],
  loading: false,
  error: '',
  isPanelOpen: false,
  addDockerForm: { ...defaultDockerForm },
  addCustomForm: { ...defaultCustomForm },
  activeAddTab: 'docker',

  setPanelOpen: (open) => set({ isPanelOpen: open }),
  setServers: (servers) => set({ servers }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
  setActiveAddTab: (tab) => set({ activeAddTab: tab }),
  setAddDockerForm: (form) =>
    set((s) => ({ addDockerForm: { ...s.addDockerForm, ...form } })),
  setAddCustomForm: (form) =>
    set((s) => ({ addCustomForm: { ...s.addCustomForm, ...form } })),
  resetAddDockerForm: () => set({ addDockerForm: { ...defaultDockerForm } }),
  resetAddCustomForm: () => set({ addCustomForm: { ...defaultCustomForm } }),

  fetchServers: async () => {
    set({ loading: true, error: '' });
    try {
      // Fetch both builtin/custom and docker servers in parallel
      const [mcpRes, dockerRes, dockerToolsRes] = await Promise.all([
        fetch(`${LLM_BASE}/mcp/servers`).then((r) => r.json()).catch(() => ({ builtin: [], custom: [] })),
        fetch(`${LLM_BASE}/mcp/docker/servers`).then((r) => r.json()).catch(() => ({ servers: [] })),
        fetch(`${LLM_BASE}/mcp/docker/tools`).then((r) => r.json()).catch(() => ({ tools: [] })),
      ]);

      const dockerToolMap = new Map<string, string[]>();
      for (const item of dockerToolsRes.tools || []) {
        const server = item.server as string;
        const tool = item.tool as string;
        if (!dockerToolMap.has(server)) dockerToolMap.set(server, []);
        dockerToolMap.get(server)?.push(tool);
      }

      const all: MCPServerInfo[] = [];

      // Builtins
      for (const s of mcpRes.builtin || []) {
        all.push({
          name: s.name,
          type: 'builtin',
          status: s.enabled === false ? 'disabled' : 'running',
          tools: s.tools || [],
          description: s.description,
          enabled: s.enabled !== false,
        });
      }

      // Custom
      for (const s of mcpRes.custom || []) {
        all.push({
          name: s.name,
          type: 'custom',
          status: s.enabled ? 'running' : 'disabled',
          tools: s.tools || [],
          description: s.description,
          base_url: s.base_url,
          enabled: s.enabled,
        });
      }

      // Docker
      for (const s of dockerRes.servers || []) {
        const liveTools = dockerToolMap.get(s.name);
        all.push({
          name: s.name,
          type: 'docker',
          status: s.status || 'stopped',
          tools: liveTools && liveTools.length > 0 ? liveTools : (s.tools || []),
          command: s.command,
          args: s.args,
          enabled: s.enabled !== false,
        });
      }

      set({ servers: all, loading: false });
    } catch (e: any) {
      set({ error: e.message || 'Failed to fetch MCP servers', loading: false });
    }
  },

  addDockerServer: async () => {
    const { addDockerForm, fetchServers } = get();
    if (!addDockerForm.name || !addDockerForm.image) {
      set({ error: 'Name and Docker image are required' });
      return;
    }
    set({ loading: true, error: '' });
    try {
      // Build docker args: ["run", "-i", "--rm", ...extraArgs, image]
      const extraArgs = addDockerForm.args
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean);

      const envPairs: Record<string, string> = {};
      addDockerForm.env
        .split('\n')
        .map((s) => s.trim())
        .filter(Boolean)
        .forEach((line) => {
          const idx = line.indexOf('=');
          if (idx > 0) {
            envPairs[line.substring(0, idx).trim()] = line.substring(idx + 1).trim();
          }
        });

      const isNpx = addDockerForm.commandType === 'npx';
      // Detect windows to append .cmd to avoid generic OS subprocess errors.
      const isWin = navigator.userAgent.toLowerCase().includes('win');
      const cmd = isNpx ? (isWin ? 'npx.cmd' : 'npx') : 'docker';
      
      const baseArgs = isNpx
        ? ['-y', addDockerForm.image, ...extraArgs]
        : ['run', '-i', '--rm', ...extraArgs, addDockerForm.image];

      const body = {
        name: addDockerForm.name,
        command: cmd,
        args: baseArgs,
        env: envPairs,
        auto_start: true,
      };

      const res = await fetch(`${LLM_BASE}/mcp/docker/servers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }

      set({ addDockerForm: { ...defaultDockerForm } });
      await fetchServers();
    } catch (e: any) {
      set({ error: e.message || 'Failed to add Docker MCP server', loading: false });
    }
  },

  addCustomServer: async () => {
    const { addCustomForm, fetchServers } = get();
    if (!addCustomForm.name || !addCustomForm.base_url) {
      set({ error: 'Name and Base URL are required' });
      return;
    }
    set({ loading: true, error: '' });
    try {
      const tools = addCustomForm.tools
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);

      const body = {
        name: addCustomForm.name,
        base_url: addCustomForm.base_url,
        description: addCustomForm.description,
        tools,
        enabled: true,
        auth_header: addCustomForm.auth_header || null,
      };

      const res = await fetch(`${LLM_BASE}/mcp/servers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }

      set({ addCustomForm: { ...defaultCustomForm } });
      await fetchServers();
    } catch (e: any) {
      set({ error: e.message || 'Failed to add Custom MCP server', loading: false });
    }
  },

  removeServer: async (name: string, type: string) => {
    set({ loading: true, error: '' });
    try {
      const url =
        type === 'docker'
          ? `${LLM_BASE}/mcp/docker/servers/${encodeURIComponent(name)}`
          : `${LLM_BASE}/mcp/servers/${encodeURIComponent(name)}`;

      const res = await fetch(url, { method: 'DELETE' });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      await get().fetchServers();
    } catch (e: any) {
      set({ error: e.message || 'Failed to remove server', loading: false });
    }
  },

  restartDockerServer: async (name: string) => {
    set({ loading: true, error: '' });
    try {
      const res = await fetch(`${LLM_BASE}/mcp/docker/servers/${encodeURIComponent(name)}/restart`, {
        method: 'POST',
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      await get().fetchServers();
    } catch (e: any) {
      set({ error: e.message || 'Failed to restart server', loading: false });
    }
  },

  toggleServerEnabled: async (name: string, enabled: boolean) => {
    set({ loading: true, error: '' });
    try {
      const res = await fetch(`${LLM_BASE}/mcp/servers/${encodeURIComponent(name)}/enabled`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      await get().fetchServers();
    } catch (e: any) {
      set({ error: e.message || 'Failed to update server status', loading: false });
    }
  },

  toggleDockerTool: async (name: string) => {
    set({ loading: true, error: '' });
    try {
      const res = await fetch(`${LLM_BASE}/tools/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server: name }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      await get().fetchServers();
    } catch (e: any) {
      set({ error: e.message || 'Failed to toggle docker tool', loading: false });
    }
  },
}));
