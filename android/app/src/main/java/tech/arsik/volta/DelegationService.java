package tech.arsik.volta;

import com.google.androidbrowserhelper.locationdelegation.LocationDelegationExtraCommandHandler;

/**
 * Lets Chrome ask this app for the Android location permission, so "Use my location" in the
 * web app shows the system prompt for Volta Go instead of a browser prompt.
 */
public class DelegationService extends com.google.androidbrowserhelper.trusted.DelegationService {
    public DelegationService() {
        registerExtraCommandHandler(new LocationDelegationExtraCommandHandler());
    }
}
