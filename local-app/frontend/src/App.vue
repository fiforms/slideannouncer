<script setup>
import { product } from './product.js'
</script>

<template>
  <router-view />
  <component :is="product.overlay" v-if="product.overlay" />
</template>
