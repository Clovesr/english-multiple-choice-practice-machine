import { createRouter, createWebHistory } from 'vue-router'
import AiAssistant from './components/AiAssistant.vue'
import DashboardView from './views/DashboardView.vue'
import ImportView from './views/ImportView.vue'
import LibraryView from './views/LibraryView.vue'
import PracticeView from './views/PracticeView.vue'
import SettingsView from './views/SettingsView.vue'
import WrongView from './views/WrongView.vue'
import VocabularyView from './views/VocabularyView.vue'
import TrashView from './views/TrashView.vue'
import ResourcesView from './views/ResourcesView.vue'
import ReaderView from './views/ReaderView.vue'
import StudyView from './views/StudyView.vue'
import WordbooksView from './views/WordbooksView.vue'
import VocabModuleView from './views/VocabModuleView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: DashboardView },
    { path: '/library', component: LibraryView },
    { path: '/resources', component: ResourcesView },
    { path: '/resources/:id/read', component: ReaderView },
    {
      // 词汇模块统一外壳；子路径保持绝对路径，历史链接全部兼容
      path: '/vocab',
      component: VocabModuleView,
      redirect: '/study',
      children: [
        { path: '/study', component: StudyView },
        { path: '/wordbooks', component: WordbooksView },
        { path: '/vocabulary', component: VocabularyView },
      ],
    },
    { path: '/practice/:id', component: PracticeView },
    { path: '/wrong', component: WrongView },
    { path: '/imports', component: ImportView },
    { path: '/assistant', component: AiAssistant },
    { path: '/settings', component: SettingsView },
    { path: '/trash', component: TrashView },
  ],
})
