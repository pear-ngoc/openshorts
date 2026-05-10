import React, { useState, useEffect } from 'react';
import { Key, Eye, EyeOff, Check } from 'lucide-react';

export default function KeyInput({
  onKeySet,
  onBaseUrlSet,
  onProviderSet,
  onModelSet,
  savedKey,
  savedBaseUrl,
  savedProvider,
  savedModel,
}) {
  const [provider, setProvider] = useState(savedProvider || 'gemini');
  const [key, setKey] = useState(savedKey || '');
  const [baseUrl, setBaseUrl] = useState(savedBaseUrl || '');
  const [model, setModel] = useState(savedModel || '');
  const [isVisible, setIsVisible] = useState(false);
  const [isSaved, setIsSaved] = useState(!!savedKey);

  useEffect(() => { if (savedKey) setKey(savedKey); }, [savedKey]);
  useEffect(() => { if (savedBaseUrl !== undefined) setBaseUrl(savedBaseUrl || ''); }, [savedBaseUrl]);
  useEffect(() => { setProvider(savedProvider || 'gemini'); }, [savedProvider]);
  useEffect(() => { setModel(savedModel || ''); }, [savedModel]);

  const handleSave = () => {
    if (key.trim().length > 0) {
      onKeySet(key);
      onBaseUrlSet && onBaseUrlSet(baseUrl);
      onProviderSet && onProviderSet(provider);
      onModelSet && onModelSet(model);
      setIsSaved(true);
    }
  };

  const handleProviderChange = (e) => {
    setProvider(e.target.value);
    setIsSaved(false);
  };

  const isGemini = provider === 'gemini';
  const apiKeyLabel = isGemini ? 'Gemini API Key' : 'API Key';
  const apiKeyPlaceholder = isGemini ? 'AIzaSy...' : 'sk-...';
  const baseUrlPlaceholder = isGemini
    ? 'https://generativelanguage.googleapis.com'
    : 'http://host.docker.internal:8045/v1';
  const baseUrlHelper = isGemini
    ? 'Optional. Leave empty to use the default Gemini API endpoint.'
    : 'Required for OpenAI-compatible. Use http://host.docker.internal:8045/v1 in Docker or http://127.0.0.1:8045/v1 outside Docker.';
  const modelDefault = isGemini ? 'gemini-2.5-flash' : 'gemini-3-flash';
  const modelPlaceholder = isGemini ? 'gemini-2.5-flash' : 'gemini-3-flash';

  return (
    <div className="bg-surface border border-white/5 rounded-2xl p-6 mb-8 animate-[fadeIn_0.5s_ease-out]">
      <div className="flex items-center gap-3 mb-4">
        <div className="p-2 bg-accent/20 rounded-lg text-accent">
          <Key size={20} />
        </div>
        <h2 className="text-lg font-semibold">AI Settings</h2>
      </div>

      {/* LLM Provider */}
      <div className="flex items-center gap-2 mb-3">
        <span className="text-sm text-zinc-400">LLM Provider</span>
      </div>
      <div className="flex gap-3 mb-4">
        <button
          onClick={() => handleProviderChange({ target: { value: 'gemini' } })}
          className={`flex-1 py-2.5 px-4 rounded-xl border text-sm font-medium transition-all ${
            isGemini
              ? 'bg-primary/20 border-primary/40 text-primary'
              : 'bg-white/5 border-white/10 text-zinc-400 hover:text-white hover:bg-white/10'
          }`}
        >
          Gemini Native
        </button>
        <button
          onClick={() => handleProviderChange({ target: { value: 'openai_compatible' } })}
          className={`flex-1 py-2.5 px-4 rounded-xl border text-sm font-medium transition-all ${
            !isGemini
              ? 'bg-violet-500/20 border-violet-500/40 text-violet-400'
              : 'bg-white/5 border-white/10 text-zinc-400 hover:text-white hover:bg-white/10'
          }`}
        >
          OpenAI Compatible
        </button>
      </div>

      {/* API Key */}
      <div className="flex items-center gap-2 mb-2">
        <Key size={14} className="text-zinc-500 shrink-0" />
        <span className="text-sm text-zinc-400">{apiKeyLabel}</span>
        {!isGemini && <span className="text-xs text-violet-400 bg-violet-500/10 px-2 py-0.5 rounded">Required</span>}
      </div>
      <div className="flex gap-3 mb-4">
        <div className="relative flex-1">
          <input
            type={isVisible ? "text" : "password"}
            value={key}
            onChange={(e) => {
              setKey(e.target.value);
              setIsSaved(false);
            }}
            placeholder={apiKeyPlaceholder}
            className="input-field pr-12 font-mono"
          />
          <button
            onClick={() => setIsVisible(!isVisible)}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-white transition-colors"
          >
            {isVisible ? <EyeOff size={18} /> : <Eye size={18} />}
          </button>
        </div>
        <button
          onClick={handleSave}
          disabled={!key || isSaved}
          className={`px-6 rounded-xl font-medium transition-all flex items-center gap-2 ${isSaved
              ? 'bg-green-500/20 text-green-400 cursor-default'
              : 'bg-primary hover:bg-blue-600 text-white shadow-lg shadow-primary/20'
            }`}
        >
          {isSaved ? <><Check size={18} /> Ready</> : 'Set Key'}
        </button>
      </div>

      {/* Base URL */}
      <div className="flex items-center gap-2 mb-2">
        <Key size={14} className="text-zinc-500 shrink-0" />
        <span className="text-sm text-zinc-400">Base URL</span>
      </div>
      <div className="flex gap-3 mb-2">
        <div className="flex-1">
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => {
              setBaseUrl(e.target.value);
              if (isSaved) setIsSaved(false);
            }}
            placeholder={baseUrlPlaceholder}
            className="input-field text-sm font-mono"
          />
        </div>
      </div>
      <p className="mt-1 mb-4 text-xs text-zinc-500">
        {baseUrlHelper}
      </p>

      {/* Model */}
      <div className="flex items-center gap-2 mb-2">
        <Key size={14} className="text-zinc-500 shrink-0" />
        <span className="text-sm text-zinc-400">Model</span>
      </div>
      <div className="flex gap-3 mb-3">
        <div className="flex-1">
          <input
            type="text"
            value={model}
            onChange={(e) => {
              setModel(e.target.value);
              if (isSaved) setIsSaved(false);
            }}
            placeholder={modelPlaceholder}
            className="input-field text-sm font-mono"
          />
        </div>
      </div>
      <p className="text-xs text-zinc-500">
        Default: {modelDefault}. Leave empty to use the default.
      </p>

      {/* Links */}
      <div className="mt-4 pt-4 border-t border-white/5">
        {isGemini ? (
          <p className="text-xs text-zinc-500">
            Your key is stored locally in your browser.
            <br />
            <a
              href="https://aistudio.google.com/app/apikey"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              Get your free Gemini API Key here →
            </a>
          </p>
        ) : (
          <p className="text-xs text-zinc-500">
            OpenAI-compatible mode uses <code className="text-violet-400">/v1/chat/completions</code>.
            Works with Antigravity Tool, LiteLLM, OpenRouter, and other OpenAI-compatible proxies.
          </p>
        )}
      </div>
    </div>
  );
}
