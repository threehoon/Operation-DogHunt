<script setup lang="ts">
// 报错提示末尾的“编号 xxx，查看日志”：点开调用日志页并直接定位到这次调用。
defineProps<{ id: string }>()
</script>

<template>
  <RouterLink :to="{ path: '/logs', query: { trace: id } }" class="link trace-link">编号 <span class="mono">{{ id }}</span>，查看日志</RouterLink>
</template>

<style scoped>
.trace-link { margin-left: 6px; white-space: nowrap; }
</style>
