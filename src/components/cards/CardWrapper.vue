<template>
  <div class="card">
    <h2>{{ card.id }}</h2>

    <!-- Card content slot -->
    <slot />

    <!-- Copy + Gear buttons -->
    <div class="inline-actions">
      <el-button size="small" @click="copyCard">复制</el-button>
      <el-button size="small" type="warning" @click="showDetail = !showDetail">⚙</el-button>
    </div>

    <!-- Detail panel -->
    <div v-show="showDetail" class="card-detail-panel">
      <!-- Copy detail checkboxes -->
      <div v-if="card.copyDetail && Object.keys(card.copyDetail).length">
        <div class="info-text">复制时包含：</div>
        <label v-for="(label, key) in card.copyDetail" :key="key" class="card-visibility-item">
          <input
            type="checkbox"
            :checked="cardStore.copyDetail[card.id][key]"
            @change="e => { cardStore.copyDetail[card.id][key] = e.target.checked; cardStore.save() }"
          >
          {{ label }}
        </label>
      </div>

      <!-- Per-card settings -->
      <slot name="detail" />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useCardStore } from '@/stores/cardStore'

const props = defineProps({
  card: { type: Object, required: true }
})

const cardStore = useCardStore()
const showDetail = ref(false)

function copyCard() {
  // TODO: implement per-card copy
  console.log('Copy card:', props.card.copyKey)
}
</script>

<style scoped>
.inline-actions {
  margin-top: 8px;
  display: flex;
  gap: 8px;
  align-items: center;
}

.card-detail-panel {
  margin-top: 8px;
  border-top: 1px solid var(--border);
  padding-top: 8px;
  font-size: 13px;
}

.card-visibility-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  cursor: pointer;
}
</style>
