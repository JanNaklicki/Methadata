import type { FragmentLayer } from 'sigma/rendering'

const ATLAS_SIZE = 2048
const SLOT_SIZE = 128
const GRID = ATLAS_SIZE / SLOT_SIZE // 16x16 = 256 thumbnails
const SLOT_PAD = 2
const TEXTURE_UNIT = 6

type Rect = [number, number, number, number]

/**
 * Custom Sigma 4 WebGL2 fragment layer that renders circular/shaped node
 * thumbnails from an offscreen 2D canvas texture atlas.
 * Nodes without an `image` URL (or before the image loads) emit transparent
 * fragments so `layerFill` remains visible as a clean fallback.
 */
export function layerThumbnail(): FragmentLayer {
  return {
    name: 'thumbnail',
    uniforms: [{ name: 'u_thumbAtlas', type: 'sampler2D' }],
    attributes: [
      {
        name: 'a_thumbRect',
        size: 4,
        type: WebGL2RenderingContext.FLOAT,
        source: 'image',
        defaultValue: 0,
      },
    ],
    glsl: `
vec4 layer_thumbnail(vec4 thumbRect, sampler2D thumbAtlas) {
  if (thumbRect.z <= thumbRect.x) {
    return vec4(0.0);
  }
  vec2 t = clamp(vec2(context.uv.x * 0.5 + 0.5, 0.5 - context.uv.y * 0.5), 0.0, 1.0);
  vec2 atlasUV = mix(thumbRect.xy, thumbRect.zw, t);
  vec4 tex = texture(thumbAtlas, atlasUV);
  float vig = 1.0 - smoothstep(0.68, 1.0, length(context.uv)) * 0.2;
  return vec4(tex.rgb * vig, tex.a);
}
`,
    lifecycle(ctx) {
      const { gl } = ctx
      let texture: WebGLTexture | null = null
      let canvas: HTMLCanvasElement | null = null
      let context2d: CanvasRenderingContext2D | null = null
      let dirty = false
      let disposed = false
      let nextSlot = 0
      const rects = new Map<string, Rect>()
      const pending = new Set<string>()
      const failed = new Set<string>()

      function ensureCanvas() {
        if (!canvas) {
          canvas = document.createElement('canvas')
          canvas.width = ATLAS_SIZE
          canvas.height = ATLAS_SIZE
          context2d = canvas.getContext('2d')
        }
        return context2d
      }

      function uploadAtlas() {
        if (!texture || !canvas || !dirty) return
        gl.activeTexture(gl.TEXTURE0 + TEXTURE_UNIT)
        gl.bindTexture(gl.TEXTURE_2D, texture)
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, canvas)
        gl.generateMipmap(gl.TEXTURE_2D)
        dirty = false
      }

      function loadImage(url: string) {
        if (pending.has(url) || failed.has(url) || rects.has(url) || nextSlot >= GRID * GRID) return
        pending.add(url)
        const img = new Image()
        img.crossOrigin = 'anonymous'
        img.onload = () => {
          pending.delete(url)
          if (disposed || nextSlot >= GRID * GRID || !img.naturalWidth || !img.naturalHeight) return
          const c2d = ensureCanvas()
          if (!c2d) return
          const slot = nextSlot++
          const col = slot % GRID
          const row = Math.floor(slot / GRID)
          const dx = col * SLOT_SIZE
          const dy = row * SLOT_SIZE

          // Center-crop ("object-fit: cover") into the padded slot.
          const side = Math.min(img.naturalWidth, img.naturalHeight)
          const sx = (img.naturalWidth - side) / 2
          const sy = (img.naturalHeight - side) / 2
          c2d.fillStyle = '#0f1624'
          c2d.fillRect(dx, dy, SLOT_SIZE, SLOT_SIZE)
          c2d.drawImage(img, sx, sy, side, side, dx + SLOT_PAD, dy + SLOT_PAD, SLOT_SIZE - SLOT_PAD * 2, SLOT_SIZE - SLOT_PAD * 2)

          const u0 = (dx + SLOT_PAD) / ATLAS_SIZE
          const v0 = (dy + SLOT_PAD) / ATLAS_SIZE
          const u1 = (dx + SLOT_SIZE - SLOT_PAD) / ATLAS_SIZE
          const v1 = (dy + SLOT_SIZE - SLOT_PAD) / ATLAS_SIZE
          rects.set(url, [u0, v0, u1, v1])
          dirty = true
          ctx.requestRefresh()
        }
        img.onerror = () => {
          pending.delete(url)
          failed.add(url)
        }
        img.src = url
      }

      return {
        init() {
          texture = gl.createTexture()
          gl.activeTexture(gl.TEXTURE0 + TEXTURE_UNIT)
          gl.bindTexture(gl.TEXTURE_2D, texture)
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR)
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
          // Seed a 1x1 transparent pixel so the sampler is complete before images arrive.
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0, 0]))
        },
        beforeRender() {
          if (!texture) return
          if (dirty) uploadAtlas()
          gl.activeTexture(gl.TEXTURE0 + TEXTURE_UNIT)
          gl.bindTexture(gl.TEXTURE_2D, texture)
          const loc = ctx.getUniformLocation('u_thumbAtlas')
          if (loc) gl.uniform1i(loc, TEXTURE_UNIT)
        },
        getAttributeData(data) {
          const url = typeof data.image === 'string' ? data.image : ''
          if (!url) return [0, 0, 0, 0]
          const rect = rects.get(url)
          if (rect) return rect
          loadImage(url)
          return [0, 0, 0, 0]
        },
        kill() {
          disposed = true
          if (texture) {
            gl.deleteTexture(texture)
            texture = null
          }
          canvas = null
          context2d = null
          rects.clear()
          pending.clear()
          failed.clear()
        },
      }
    },
  }
}

/**
 * Custom Sigma 4 WebGL2 fragment layer that draws a crisp anti-aliased colored
 * border ring around each node plus a subtle dark inner hairline so thumbnails
 * separate cleanly from the ring.
 */
export function layerNodeBorder(): FragmentLayer {
  return {
    name: 'border',
    uniforms: [],
    attributes: [
      {
        name: 'a_borderColor',
        size: 4,
        type: WebGL2RenderingContext.UNSIGNED_BYTE,
        normalized: true,
        source: 'borderColor',
        defaultValue: '#a5e7ff',
      },
      {
        name: 'a_borderSize',
        size: 1,
        type: WebGL2RenderingContext.FLOAT,
        source: 'borderSize',
        defaultValue: 2.2,
      },
    ],
    glsl: `
vec4 layer_border(vec4 borderColor, float borderSize) {
  float bw = max(borderSize, 0.0) * context.pixelToUV;
  if (bw <= 0.0001) {
    return vec4(0.0);
  }
  float ring = smoothstep(-bw - context.aaWidth, -bw + context.aaWidth, context.sdf);
  float sepOuter = -bw;
  float sepInner = -bw - 1.3 * context.pixelToUV;
  float sep = smoothstep(sepInner - context.aaWidth, sepInner + context.aaWidth, context.sdf) * (1.0 - ring);
  vec4 sepColor = vec4(0.035, 0.06, 0.10, sep * 0.72);
  vec4 ringColor = vec4(borderColor.rgb, borderColor.a * ring);
  return blendOver(sepColor, ringColor);
}
`,
  }
}
