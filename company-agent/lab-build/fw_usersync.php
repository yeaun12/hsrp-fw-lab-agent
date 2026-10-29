<?php
require_once("config.inc");
require_once("auth.inc");
global $config;
foreach ($config['system']['user'] as $u) {
 if ($u['name'] == 'admin') local_user_set($u);
}
echo "USER-SYNCED\n";
