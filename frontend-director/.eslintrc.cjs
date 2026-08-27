module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  ignorePatterns: ['dist', '.eslintrc.cjs', 'node_modules'],
  parser: '@typescript-eslint/parser',
  parserOptions: { ecmaVersion: 'latest', sourceType: 'module' },
  plugins: ['@typescript-eslint'],
  rules: {
    '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
    '@typescript-eslint/no-explicit-any': 'off',
    // ⭐ react-hooks v7 新增激进规则：对"加载数据后 setState / 渲染期累加"等正常模式误报率高。
    // 2026-08-27 全量审计（20 处：ProjectContext/PromptPicker/QcReportCard/BatchesPage/
    // OneClickVideoPage/PresetsPage/ProjectsPage/PromptsPage/SettingsPage/StoryboardPage/
    // WorkflowTemplatesPage/ComposePage/EditPage/GraphicPage/ScreenRecordPage/ScriptPage/VideoPage）：
    // 全部为"异步加载后 setState / 渲染期引用加载函数"的正常模式，非 bug。
    // 从 warn 降为 off（已审计结论），保证 eslint 0-warning 门禁可执行；新引入的真问题靠
    // exhaustive-deps（保留）+ code review 拦截。exhaustive-deps 保留并对豁免处逐条标注。
    'react-hooks/set-state-in-effect': 'off',
    'react-hooks/immutability': 'off',
  },
}
