# OPNsense PHP-FPM Web UI Performance Optimization

## Problem

OPNsense web UI was sluggish and slow to respond, even though system resources (CPU, memory, network) were not constrained.

## Root Cause

Default PHP-FPM settings were too conservative:

- `pm.max_children = 5` - Only 5 PHP processes could handle requests simultaneously
- `pm.start_servers = 2` - Only 2 processes started initially
- `pm.min_spare_servers = 1` - Only 1 spare process maintained
- `pm.max_spare_servers = 3` - Maximum of 3 spare processes

This caused request queuing when navigating the web UI, especially with multiple tabs or complex pages.

## Solution

Increased PHP-FPM process limits to allow better concurrency:

```ini
pm = dynamic
pm.max_children = 10        # Increased from 5
pm.start_servers = 4        # Increased from 2
pm.min_spare_servers = 2    # Increased from 1
pm.max_spare_servers = 6    # Increased from 3
pm.max_requests = 500
```

## Implementation

### Step 1: Backup Current Configuration

```bash
cp /usr/local/etc/php-fpm.d/www.conf /usr/local/etc/php-fpm.d/www.conf.backup
```

### Step 2: Update PHP-FPM Settings

```bash
sed -i '' \
  -e 's/^pm\.max_children = 5/pm.max_children = 10/' \
  -e 's/^pm\.start_servers = 2/pm.start_servers = 4/' \
  -e 's/^pm\.min_spare_servers = 1/pm.min_spare_servers = 2/' \
  -e 's/^pm\.max_spare_servers = 3/pm.max_spare_servers = 6/' \
  /usr/local/etc/php-fpm.d/www.conf
```

### Step 3: Verify Changes

```bash
grep -E "^(pm\.|pm =)" /usr/local/etc/php-fpm.d/www.conf
```

Expected output:

```ini
pm = dynamic
pm.max_children = 10
pm.start_servers = 4
pm.min_spare_servers = 2
pm.max_spare_servers = 6
```

### Step 4: Restart Web GUI (OPNsense-specific)

```bash
configctl webgui restart
```

**Note:** On OPNsense, do NOT use `service php-fpm restart` or `service nginx restart` - these commands don't work. Use `configctl webgui restart` instead.

## One-Line Command

```bash
cp /usr/local/etc/php-fpm.d/www.conf /usr/local/etc/php-fpm.d/www.conf.backup && sed -i '' -e 's/^pm\.max_children = 5/pm.max_children = 10/' -e 's/^pm\.start_servers = 2/pm.start_servers = 4/' -e 's/^pm\.min_spare_servers = 1/pm.min_spare_servers = 2/' -e 's/^pm\.max_spare_servers = 3/pm.max_spare_servers = 6/' /usr/local/etc/php-fpm.d/www.conf && echo "=== Updated settings ===" && grep -E "^(pm\.|pm =)" /usr/local/etc/php-fpm.d/www.conf && configctl webgui restart
```

## Persistence After Updates

**Important:** OPNsense updates may overwrite `/usr/local/etc/php-fpm.d/www.conf`, reverting these changes.

### Create Restoration Script

```bash
cat > /root/restore-php-fpm-settings.sh << 'EOF'
#!/bin/sh
# Restore PHP-FPM settings after OPNsense updates

sed -i '' \
  -e 's/^pm\.max_children = .*/pm.max_children = 10/' \
  -e 's/^pm\.start_servers = .*/pm.start_servers = 4/' \
  -e 's/^pm\.min_spare_servers = .*/pm.min_spare_servers = 2/' \
  -e 's/^pm\.max_spare_servers = .*/pm.max_spare_servers = 6/' \
  /usr/local/etc/php-fpm.d/www.conf

configctl webgui restart
echo "PHP-FPM settings restored"
EOF

chmod +x /root/restore-php-fpm-settings.sh
```

### After Updates

1. Check if settings were overwritten:

   ```bash
   grep -E "^(pm\.|pm =)" /usr/local/etc/php-fpm.d/www.conf
   ```

2. If values reverted to defaults (5, 2, 1, 3), run:

   ```bash
   /root/restore-php-fpm-settings.sh
   ```

### Manual Restore from Backup

If needed, restore from backup:

```bash
cp /usr/local/etc/php-fpm.d/www.conf.backup /usr/local/etc/php-fpm.d/www.conf
configctl webgui restart
```

## System Requirements

- **Tested on:** OPNsense 25.7.8-amd64, FreeBSD 14.3-RELEASE-p5
- **Memory:** Ensure sufficient RAM available (each PHP-FPM child uses ~20-50MB)
- **CPU:** Low CPU usage is fine - this optimization addresses concurrency, not CPU load

## Results

After applying these changes, the OPNsense web UI responsiveness improved significantly. The system was not resource-constrained (CPU ~24%, plenty of RAM for ZFS), so the bottleneck was PHP-FPM process limits.

## Further Optimization (Optional)

If 10 max_children works well, you can increase further:

- `pm.max_children = 15` or `20`
- Adjust other values proportionally
- Monitor memory usage: `top` or `htop`
- Check PHP-FPM status: `ps aux | grep php-fpm`

## References

- PHP-FPM configuration: `/usr/local/etc/php-fpm.d/www.conf`
- OPNsense service management: Use `configctl` instead of `service` command
- Backup location: `/usr/local/etc/php-fpm.d/www.conf.backup`
